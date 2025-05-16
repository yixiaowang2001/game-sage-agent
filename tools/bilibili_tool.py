from typing import Any, Type
import asyncio
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Any, Type, Optional

from core.llm import llm
from utils.bilibili_utils.searcher import BilibiliSearcher
from utils.bilibili_utils.comments_crawler import BilibiliCommentsCrawler
from utils.bilibili_utils.video_info_extractor import BilibiliVideoInfoExtractor
from utils.cookies_tool import async_load_cookies
from configs.logger_config import get_logger
from prompts.summarizer_prompts import BILIBILI_SUMMARY_PROMPT
from configs.tool_config import BILIBILI_TIMEOUT_SECONDS

logger = get_logger("tools.bilibili_tool")

OUTPUT_TEMPLATE = """##### 视频标题
{title}

##### 视频描述
{description}

##### 视频标签
{tags}

##### 视频文稿
{transcript}

##### 评论
{comments}

##### 是否有误
视频信息错误：{video_info_error}
评论获取错误：{comment_info_error}
"""


async def _process_single_video(video_url, cookies=None, llm_correction=True):
    tasks = []
    if cookies:
        tasks.append(BilibiliCommentsCrawler(cookies).get_comments(video_url))
    tasks.append(BilibiliVideoInfoExtractor(video_url).get_video_info(video_url, llm_correction))

    results = await asyncio.gather(*tasks)
    if cookies:
        comment_info, video_info = results[0], results[1]
    else:
        video_info = results[0]
        comment_info = None

    return video_info, comment_info


async def _format_single_output(video_info, comment_info):
    description = video_info["description"]
    tags = video_info["tags"]
    title = video_info["title"]
    transcript = video_info["transcript"]
    video_info_error = video_info["error"]

    comments = comment_info["comments"]
    comment_info_error = comment_info["error"]

    video_error_display = video_info_error if video_info_error else '无'
    comment_error_display = comment_info_error if comment_info_error else '无'

    return OUTPUT_TEMPLATE.format(
        title=title,
        description=description,
        tags=tags,
        transcript=transcript,
        comments=comments,
        video_info_error=video_error_display,
        comment_info_error=comment_error_display,
    )


async def _llm_summary(query, video_text_content):
    if not video_text_content:
        return video_text_content
    if llm is None:
        logger.error("LLM instance is not available. Skipping summarization.")
        return video_text_content

    try:
        prompt_value = BILIBILI_SUMMARY_PROMPT.format_prompt(
            query=query,
            video_all=video_text_content
        )
        response = await llm.ainvoke(prompt_value)
        summarized_text = response.content
        return summarized_text
    except Exception as e:
        logger.exception(
            f"Error during LLM summary for query '{query}' with content preview '{str(video_text_content)[:200]}...': {e}")
        return video_text_content


async def _assemble_outputs(query, video_output_list, llm_summary: bool):
    if not video_output_list:
        if llm_summary:
            return "没有找到相关视频内容可供总结。"
        else:
            return "没有找到相关视频内容。"

    if not llm_summary:
        raw_concatenation = ""
        for i, video_text in enumerate(video_output_list):
            raw_concatenation += f"#### 视频 {i + 1}\n{video_text}\n\n"
        return f"针对用户问题：{query}，以下是来自B站 {len(video_output_list)} 个视频的具体内容：\n\n{raw_concatenation.strip()}"

    individual_processed_texts = []
    logger.info(f"LLM summary requested. Starting individual processing for {len(video_output_list)} video outputs.")

    for i, single_video_full_text in enumerate(video_output_list):
        logger.info(
            f"Processing content for video {i + 1}/{len(video_output_list)} based on query: '{query}'.")
        processed_text = await _llm_summary(query, single_video_full_text)
        individual_processed_texts.append(processed_text)

    final_assembled_text = f"针对用户问题：{query}，以下是基于B站 {len(individual_processed_texts)} 个视频内容的分析与摘要：\n\n"
    for i, processed_text in enumerate(individual_processed_texts):
        final_assembled_text += f"--- 视频 {i + 1} 内容解读 ---\n{processed_text}\n\n"

    return final_assembled_text.strip()


class BilibiliToolInput(BaseModel):
    query: str = Field(description="B站视频搜索关键词，可以是游戏名称、角色技能、攻略类型等。越具体的关键词搜索结果越精准")
    timeout_seconds: Optional[float] = Field(
        default=None,
        description="工具执行的最大时间限制（秒）。若不指定，将使用系统默认值。超时后会返回已收集到的部分结果并标注未完成状态"
    )


class BilibiliTool(BaseTool):
    name: str = "bilibili_search_and_info"
    description: str = (
        "B站游戏视频搜索与内容分析工具。用于检索B站上的游戏攻略、解说、技巧视频并提取核心内容。"
        "此工具会返回视频标题、描述、文稿摘要、评论精选等信息，并对内容进行智能总结。"
        "适用于查找游戏打法技巧、角色攻略、装备搭配、通关要点等B站视频资源。"
        "使用时需提供明确的游戏名称和具体查询内容。"
    )
    args_schema: Type[BaseModel] = BilibiliToolInput

    def _run(self, query: str, **kwargs: Any) -> str:
        timeout_seconds = kwargs.get('timeout_seconds')
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self._arun(query=query, timeout_seconds=timeout_seconds, **kwargs))

    async def _arun(
            self,
            query: str,
            timeout_seconds: Optional[float] = None,
            run_manager=None, **kwargs: Any
    ) -> str:
        effective_timeout = (
            timeout_seconds if (timeout_seconds is not None and timeout_seconds > 0)
            else BILIBILI_TIMEOUT_SECONDS
        )

        logger.info(f"BilibiliTool._arun called for query: '{query}' with effective timeout: {effective_timeout}s")

        partial_results_list = []

        async def full_tool_operation():
            cookies = await async_load_cookies("Bilibili")
            searcher = BilibiliSearcher(cookies)
            search_results_urls = await searcher.search(query)

            if not search_results_urls:
                logger.warning(f"No Bilibili videos found related to '{query}' via async search.")
                return f"未能找到与 '{query}' 相关的Bilibili视频。"

            logger.info(f"Found {len(search_results_urls)} videos, starting individual processing...")
            process_coroutines = []
            for video_url in search_results_urls:
                process_coroutines.append(_process_single_video(video_url, cookies))

            if not process_coroutines:
                return "搜索到视频URL但未能创建处理任务。"

            processed_video_count = 0
            for i, task_future in enumerate(asyncio.as_completed(process_coroutines)):
                try:
                    video_info, comment_info = await task_future

                    safe_comment_info = comment_info
                    if safe_comment_info is None:
                        safe_comment_info = {"comments": "未能获取评论。", "error": "评论获取失败或未尝试获取。"}

                    formatted_output = await _format_single_output(video_info, safe_comment_info)
                    partial_results_list.append(formatted_output)
                    processed_video_count += 1
                    logger.info(
                        f"Successfully processed and formatted video ({processed_video_count}/{len(process_coroutines)}): {video_info.get('title', 'Unknown title')}")
                except asyncio.CancelledError:
                    logger.warning(f"A video processing task was canceled due to overall operation timeout/cancellation.")
                    break
                except Exception as e:
                    logger.error(f"Error processing individual video (URL index {i}): {e}. This video will be skipped.")

            if not partial_results_list:
                logger.warning(f"Query '{query}' failed to process any video information during data collection phase.")
                return f"未能处理与 '{query}' 相关的任何Bilibili视频信息（可能是因为所有视频处理均失败或内容不适用）。"

            logger.info(f"Data collection complete, obtained results for {len(partial_results_list)} videos. Preparing final assembly and summary.")
            final_assembled_output = await _assemble_outputs(query, partial_results_list, llm_summary=True)
            return final_assembled_output

        try:
            final_output_result = await asyncio.wait_for(full_tool_operation(), timeout=effective_timeout)
            return final_output_result
        except asyncio.TimeoutError:
            logger.warning(f"BilibiliTool processing for query '{query}' timed out (limit: {effective_timeout}s).")
            if partial_results_list:
                logger.info(f"Overall timeout. Will assemble {len(partial_results_list)} already retrieved video results (without final LLM summary).")
                fallback_assembly = await _assemble_outputs(query, partial_results_list, llm_summary=False)
                return (f"处理查询 '{query}' 超时（{effective_timeout}秒）。"
                        f"以下是已获取的部分视频的详细信息（可能未经最终摘要处理）：\n{fallback_assembly}")
            else:
                return (f"Bilibili工具处理查询 '{query}' 超时（{effective_timeout}秒）。"
                        "在超时前未能获取到任何视频的有效信息。")
        except Exception as e:
            logger.exception(f"Unexpected error (non-timeout) executing Bilibili async tool (query: '{query}'): {e}")
            return f"执行 Bilibili 异步工具时出现意外错误: {e}"


if __name__ == "__main__":
    import asyncio

    async def main_async_test():
        bili_tool_async = BilibiliTool()
        async_query = "魔兽世界 正式服 治疗"
        async_result_arun = await bili_tool_async.arun(
            async_query)
        print(async_result_arun)
    asyncio.run(main_async_test())
