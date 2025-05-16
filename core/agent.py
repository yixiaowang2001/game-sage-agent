from typing import List, Dict, Any, Optional, Callable, Union, Tuple
import asyncio
import json
import re
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from core.llm import llm
from configs.logger_config import get_logger
from prompts.agent_prompts import ROUTER_PROMPT, FINAL_SUMMARIZER_PROMPT

logger = get_logger("core.agent")


class RouterInput(BaseModel):
    query: str = Field(description="用户的查询内容")
    tools: List[str] = Field(description="可用工具的名称列表")


class ToolSelectionResult(BaseModel):
    selected_tools: List[str] = Field(description="选择的工具名称列表")
    reasoning: str = Field(description="选择这些工具的理由")
    optimized_query: str = Field(description="优化后的搜索查询")


class SummarizerInput(BaseModel):
    query: str = Field(description="原始用户查询")
    tool_results: Dict[str, str] = Field(description="各工具返回的结果，格式为{工具名: 结果内容}")


class Agent:
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        self.router_prompt = ROUTER_PROMPT
        self.summarizer_prompt = None
        self.final_summarizer_prompt = FINAL_SUMMARIZER_PROMPT

    def register_tool(self, tool: BaseTool) -> None:
        logger.info(f"Registering tool: {tool.name}")
        self.tools[tool.name] = tool

    def register_tools(self, tools: List[BaseTool]) -> None:
        for tool in tools:
            self.register_tool(tool)

    async def _route_query(self, query: str) -> Tuple[List[str], str]:
        logger.info(f"Routing query: '{query}'")
        available_tools = list(self.tools.keys())
        optimized_query = query

        if not available_tools or not self.router_prompt or not llm:
            logger.info("Router prompt not set or LLM not found, returning all available tools")
            return available_tools, optimized_query

        try:
            tools_description = []
            for name in available_tools:
                tool = self.tools[name]
                tools_description.append(f"- {name}: {tool.description}")

            tools_description_text = "\n".join(tools_description)

            prompt_value = self.router_prompt.format_prompt(
                query=query,
                tools_description=tools_description_text
            )

            logger.info("Calling LLM for query optimization and tool selection")
            response = await llm.ainvoke(prompt_value)
            response_content = response.content
            logger.info(f"Optimized query: {response_content}")

            json_match = re.search(r'```json\s*(.*?)\s*```', response_content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response_content

            try:
                result = json.loads(json_str)
                if isinstance(result, dict):
                    selected_tools = result.get("selected_tools", [])
                    reasoning = result.get("reasoning", "No reason provided")
                    optimized_query = result.get("optimized_query", query)

                    valid_tools = [t for t in selected_tools if t in available_tools]

                    if not valid_tools:
                        logger.warning(f"None of the LLM-selected tools exist: {selected_tools}, will use all tools")
                        return available_tools, optimized_query

                    logger.info(f"Query optimization result: '{query}' -> '{optimized_query}'")
                    logger.info(f"LLM selected tools: {valid_tools}, reason: {reasoning}")
                    return valid_tools, optimized_query
            except Exception as e:
                logger.error(f"JSON parsing failed: {e}, raw content: {json_str}")

        except Exception as e:
            logger.error(f"LLM routing decision failed: {e}")

        logger.info("Routing decision failed, returning all tools and original query by default")
        return available_tools, optimized_query

    async def _run_tools(self, query: str, tool_names: List[str]) -> Dict[str, str]:
        logger.info(f"Running tools: {tool_names}")
        results = {}

        async def run_single_tool(tool_name: str):
            tool = self.tools.get(tool_name)
            if not tool:
                logger.warning(f"Tool {tool_name} is not registered")
                return tool_name, f"Error: Tool {tool_name} is not registered"

            try:
                logger.info(f"Starting execution of tool {tool_name}")
                if hasattr(tool, '_arun'):
                    result = await tool._arun(query=query)
                else:
                    result = tool._run(query=query)
                logger.info(f"Tool {tool_name} execution completed")
                return tool_name, result
            except Exception as e:
                logger.error(f"Tool {tool_name} execution failed: {e}")
                return tool_name, f"Error: Tool {tool_name} execution failed: {str(e)}"

        tasks = [run_single_tool(name) for name in tool_names]

        tool_results = await asyncio.gather(*tasks)

        for tool_name, result in tool_results:
            results[tool_name] = result

        return results

    async def _summarize_individual_results(self, query: str, tool_results: Dict[str, str]) -> Dict[str, str]:
        logger.info("Starting to summarize tool results")
        summaries = {}

        for tool_name, result in tool_results.items():
            summaries[tool_name] = result

        return summaries

    async def _generate_final_response(self, query: str, summarized_results: Dict[str, str]) -> str:

        logger.info("Generating final response")

        if not summarized_results:
            return "I'm sorry, I couldn't find any information related to your query."

        if not self.final_summarizer_prompt or not llm:
            result_items = []
            for tool_name, result in summarized_results.items():
                result_items.append(f"## {tool_name} Results:\n{result}")

            combined_results = "\n\n".join(result_items)
            return f"Here is information about \"{query}\":\n\n{combined_results}"

        try:
            tool_results_text = ""
            for tool_name, result in summarized_results.items():
                tool_results_text += f"## {tool_name} Results:\n{result}\n\n"

            prompt_value = self.final_summarizer_prompt.format_prompt(
                query=query,
                tool_results=tool_results_text
            )

            logger.info("Using LLM to generate final response")
            response = await llm.ainvoke(prompt_value)
            return response.content

        except Exception as e:
            logger.error(f"LLM final summarization failed: {e}")

            result_items = []
            for tool_name, result in summarized_results.items():
                result_items.append(f"## {tool_name} Results:\n{result}")

            combined_results = "\n\n".join(result_items)
            return f"Here is information about \"{query}\":\n\n{combined_results}"

    async def process_query(self, query: str) -> str:
        logger.info(f"Processing query: '{query}'")

        try:
            selected_tool_names, optimized_query = await self._route_query(query)

            if not selected_tool_names:
                return "I cannot determine how to answer your question. Please try asking in a different way."

            tool_results = await self._run_tools(optimized_query, selected_tool_names)

            summarized_results = await self._summarize_individual_results(query, tool_results)

            final_response = await self._generate_final_response(query, summarized_results)

            return final_response

        except Exception as e:
            logger.error(f"Query processing failed: {e}")
            return f"An error occurred while processing your request: {str(e)}"

    def query(self, query: str) -> str:

        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.process_query(query))


if __name__ == "__main__":
    import asyncio
    from tools.bilibili_tool import BilibiliTool


    async def run():
        agent = Agent()

        agent.register_tool(BilibiliTool())

        test_queries = [
            "",
        ]

        for query in test_queries:
            print(f"\nTest query: '{query}'")

            tools, optimized_query = await agent._route_query(query)

            print(f"Optimized query: '{optimized_query}'")
            print(f"Selected tools: {tools}")

            result = await agent.process_query(query)
            print(f"Complete response:\n{result}\n")


    asyncio.run(run())
