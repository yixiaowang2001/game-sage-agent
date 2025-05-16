import asyncio
import re
import time
import random

import aiohttp
from fake_useragent import UserAgent

from configs.logger_config import get_logger
from configs.utils_config import (
    BILIBILI_VIEW_API_URL,
    BILIBILI_COMMENT_API_URL,
    BILIBILI_SUB_COMMENT_API_URL,
    BILIBILI_COMMENTS_REQUEST_TIMEOUT_SECONDS,
    BILIBILI_COMMENTS_MAX_CONCURRENT_REQUESTS,
    BILIBILI_COMMENTS_COMMENT_PAGE_SIZE,
    BILIBILI_COMMENTS_RETRY_DELAY_SECONDS,
    BILIBILI_COMMENTS_MAX_RETRIES,
    BILIBILI_COMMENTS_MAX_NUM,
    BILIBILI_COMMENTS_MAX_REPLIES_PER_COMMENT,
    BILIBILI_COMMENTS_LENGTH_MIN
)

logger = get_logger("utils.bilibili_utils.comment_crawler")

DEFAULT_HEADERS = {
    'User-Agent': UserAgent(platforms="desktop").random,
    'Referer': 'https://www.bilibili.com/',
    'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
    'Accept': '*/*',
    'Origin': 'https://www.bilibili.com',
    'Connection': 'keep-alive',
}


class BilibiliCommentsCrawler:
    def __init__(self, cookies):
        self.headers = DEFAULT_HEADERS.copy()
        self.cookies = cookies
        cookie_str = None
        if isinstance(self.cookies, list):
            cookie_parts = [f"{c['name']}={c['value']}" for c in self.cookies if 'name' in c and 'value' in c]
            if cookie_parts:
                cookie_str = "; ".join(cookie_parts)
                logger.debug("Formatted list of cookies into string header.")
            else:
                logger.error("Provided cookie list was empty or malformed.")
        elif isinstance(self.cookies, str) and self.cookies.strip():
            cookie_str = self.cookies.strip()
        else:
            logger.error(f"Invalid cookie format received: {type(self.cookies)}")

        if cookie_str:
            self.headers['Cookie'] = cookie_str
            logger.debug("BilibiliCommentCrawler initialized successfully with cookies.")
        else:
            logger.error("BilibiliCommentCrawler requires valid cookies. Provided cookies were invalid or empty.")

        self.semaphore = asyncio.Semaphore(BILIBILI_COMMENTS_MAX_CONCURRENT_REQUESTS)
        self.timeout = aiohttp.ClientTimeout(total=BILIBILI_COMMENTS_REQUEST_TIMEOUT_SECONDS)

    def _extract_bvid_from_url(self, video_url):
        match = re.search(r'BV([a-zA-Z0-9]{10})', video_url)
        if match:
            bvid = match.group(0)
            logger.debug(f"Extracted BVID: {bvid} from URL: {video_url}")
            return bvid
        else:
            logger.error(f"Could not extract BVID from URL: {video_url}")
            return None

    async def _get_aid_from_bvid(self, session, bvid):
        url = BILIBILI_VIEW_API_URL.format(bvid=bvid)
        last_error = None
        for attempt in range(BILIBILI_COMMENTS_MAX_RETRIES):
            try:
                async with self.semaphore:
                    logger.debug(f"Fetching AID for BVID: {bvid} (Attempt {attempt + 1})")
                    async with session.get(url, headers=self.headers, timeout=self.timeout) as response:
                        data = None
                        try:
                            data = await response.json()
                        except Exception:
                            logger.warning(
                                f"Failed to decode JSON (AID): {bvid}, Status: {response.status}, Body: {await response.text()[:100]}")

                        if response.status == 200 and data and data.get('code') == 0 and data.get('data', {}).get(
                                'aid'):
                            aid = data['data']['aid']
                            logger.debug(f"Successfully fetched AID: {aid} for BVID: {bvid}")
                            return aid
                        else:
                            error_code = data.get('code', 'N/A') if data else 'N/A'
                            error_message = data.get('message', 'No message') if data else 'No message'
                            logger.warning(
                                f"API error (AID): {bvid} Att:{attempt + 1}, Status:{response.status}, Code:{error_code}, Msg:{error_message}")
                            last_error = f"API Error (Status: {response.status}, Code: {error_code})"
                            if error_code == -404:
                                logger.error(f"BVID {bvid} not found (Code -404).")
                                return None
                            if attempt < BILIBILI_COMMENTS_MAX_RETRIES - 1:
                                await asyncio.sleep(BILIBILI_COMMENTS_RETRY_DELAY_SECONDS * (attempt + 1))
                            else:
                                logger.error(
                                    f"Failed AID fetch: {bvid} after {BILIBILI_COMMENTS_MAX_RETRIES} attempts. Last: {last_error}")
                                return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Network/Timeout error (AID): {bvid} Att:{attempt + 1}, Error: {e}")
                last_error = str(e)
                if attempt < BILIBILI_COMMENTS_MAX_RETRIES - 1:
                    await asyncio.sleep(BILIBILI_COMMENTS_RETRY_DELAY_SECONDS * (attempt + 1))
                else:
                    logger.error(
                        f"Failed AID fetch (Network/Timeout): {bvid} after {BILIBILI_COMMENTS_MAX_RETRIES} attempts.")
                    return None
            except Exception as e:
                logger.exception(f"Unexpected error (AID): {bvid} Att:{attempt + 1}, Error: {e}")
                logger.error("Stopping AID fetch due to unexpected error.")
                return None
        logger.error(f"Failed AID fetch (Loop End): {bvid}. Last: {last_error}")
        return None

    async def _fetch_comment_page(self, session, aid, page_num):
        url = BILIBILI_COMMENT_API_URL.format(aid=aid, page=page_num, size=BILIBILI_COMMENTS_COMMENT_PAGE_SIZE)
        last_exception = None
        for attempt in range(BILIBILI_COMMENTS_MAX_RETRIES):
            try:
                async with self.semaphore:
                    await asyncio.sleep(random.uniform(0.3, 1.0))
                    logger.debug(f"Fetching main page {page_num}, AID: {aid} (Att {attempt + 1})")
                    async with session.get(url, headers=self.headers, timeout=self.timeout) as response:
                        data = None
                        try:
                            data = await response.json()
                        except Exception:
                            logger.warning(
                                f"Failed JSON (Main): Pg:{page_num}, AID:{aid}, Status:{response.status}, Body:{await response.text()[:100]}")

                        if response.status == 412:
                            logger.warning(f"Status 412 (Main): Pg:{page_num}, AID:{aid}. Retrying...")
                            last_exception = Exception("Status 412")
                        elif response.status != 200:
                            logger.warning(f"Status {response.status} (Main): Pg:{page_num}, AID:{aid}. Retrying...")
                            last_exception = Exception(f"HTTP Status {response.status}")
                        elif data:
                            api_code = data.get('code')
                            if api_code == 0:
                                logger.debug(f"Success (Main): Pg:{page_num}, AID:{aid}")
                                return data.get('data', {})
                            elif api_code in [12002, -404]:
                                logger.debug(f"Comments ended/closed (Main): Pg:{page_num}, AID:{aid}, Code:{api_code}")
                                return None
                            else:
                                logger.warning(
                                    f"API error (Main): Pg:{page_num}, AID:{aid}, Code:{api_code}, Msg:{data.get('message', 'N/A')}")
                                last_exception = Exception(f"API Error {api_code}")
                        else:
                            logger.warning(f"Bad JSON (Main): Pg:{page_num}, AID:{aid}. Retrying...")
                            last_exception = Exception("Status 200 bad JSON")

                        if attempt < BILIBILI_COMMENTS_MAX_RETRIES - 1:
                            await asyncio.sleep(BILIBILI_COMMENTS_RETRY_DELAY_SECONDS * (
                                        attempt + 1) if response.status == 412 else BILIBILI_COMMENTS_RETRY_DELAY_SECONDS)
                            continue
                        else:
                            logger.error(
                                f"Failed fetch (Main): Pg:{page_num}, AID:{aid} after {BILIBILI_COMMENTS_MAX_RETRIES} attempts. Last: {last_exception}")
                            return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Network/Timeout (Main): Pg:{page_num}, AID:{aid}, Att:{attempt + 1}, Error: {e}")
                last_exception = e
                if attempt < BILIBILI_COMMENTS_MAX_RETRIES - 1:
                    await asyncio.sleep(BILIBILI_COMMENTS_RETRY_DELAY_SECONDS * (attempt + 1))
            except Exception as e:
                logger.exception(f"Unexpected error (Main): Pg:{page_num}, AID:{aid}, Att:{attempt + 1}, Error: {e}")
                logger.error("Stopping fetch (Main) due to unexpected error.")
                return None

        logger.error(f"Failed fetch (Main Loop End): Pg:{page_num}, AID:{aid}. Last: {last_exception}")
        return None

    def _process_main_replies(self, replies_data):
        processed_replies = []
        if not replies_data:
            return processed_replies
        for reply in replies_data:
            if reply and isinstance(reply.get('content'), dict) and 'message' in reply['content']:
                message = reply['content']['message']
                if len(message) >= BILIBILI_COMMENTS_LENGTH_MIN:
                    processed_replies.append({
                        'message': message,
                        'rpid': reply.get('rpid'),
                        'rcount': reply.get('rcount', 0)
                    })
        return processed_replies

    async def _fetch_sub_comments(self, session, aid, root_rpid):
        sub_comments = []
        page_num = 1
        sub_comment_page_size = 10
        fetched_count = 0
        max_pages = float('inf')
        max_replies = BILIBILI_COMMENTS_MAX_REPLIES_PER_COMMENT

        if max_replies is not None and max_replies > 0:
            max_pages = (max_replies + sub_comment_page_size - 1) // sub_comment_page_size
            logger.debug(f"Fetching max {max_replies} replies: root {root_rpid} (max_pg: {int(max_pages)})")
        else:
            logger.debug(f"Fetching all replies: root {root_rpid}")

        while True:
            if page_num > max_pages:
                logger.debug(f"Reached max pages ({int(max_pages)}) for sub-comments: root {root_rpid}")
                break

            url = BILIBILI_SUB_COMMENT_API_URL.format(aid=aid, root_rpid=root_rpid, page=page_num,
                                                      size=sub_comment_page_size)
            last_exception = None

            for attempt in range(BILIBILI_COMMENTS_MAX_RETRIES):
                should_retry = False
                try:
                    async with self.semaphore:
                        await asyncio.sleep(random.uniform(0.2, 0.6))
                        logger.debug(f"Fetching sub page {page_num}, root {root_rpid} (Att {attempt + 1})")
                        async with session.get(url, headers=self.headers, timeout=self.timeout) as response:
                            data = None
                            try:
                                data = await response.json()
                            except Exception:
                                logger.warning(
                                    f"Failed JSON (Sub): Pg:{page_num}, Root:{root_rpid}, Status:{response.status}, Body:{await response.text()[:100]}")

                            if response.status == 412:
                                logger.warning(f"Status 412 (Sub): Pg:{page_num}, Root:{root_rpid}. Retrying...")
                                last_exception = Exception("Status 412")
                                should_retry = True
                            elif response.status != 200:
                                logger.warning(
                                    f"Status {response.status} (Sub): Pg:{page_num}, Root:{root_rpid}. Retrying...")
                                last_exception = Exception(f"HTTP Status {response.status}")
                                should_retry = True
                            elif data:
                                api_code = data.get('code')
                                if api_code == 0:
                                    page_replies = data.get('data', {}).get('replies', [])
                                    if not page_replies:
                                        logger.debug(f"No more sub-comments: Root:{root_rpid}, Pg:{page_num}")
                                        return sub_comments

                                    replies_to_process = page_replies
                                    remaining_capacity = float('inf')
                                    if max_replies is not None and max_replies > 0:
                                        remaining_capacity = max_replies - fetched_count
                                        if remaining_capacity <= 0:
                                            logger.debug(f"Sub-comment limit {max_replies} reached: Root:{root_rpid}")
                                            return sub_comments
                                        replies_to_process = page_replies[:int(remaining_capacity)]

                                    processed_count_this_page = 0
                                    for sub_reply in replies_to_process:
                                        if sub_reply and isinstance(sub_reply.get('content'), dict) and 'message' in \
                                                sub_reply['content']:
                                            message = sub_reply['content']['message']
                                            if len(message) >= BILIBILI_COMMENTS_LENGTH_MIN:
                                                sub_comments.append(message)
                                                processed_count_this_page += 1

                                    fetched_count += processed_count_this_page
                                    logger.debug(
                                        f"Processed {processed_count_this_page} (Sub): Pg:{page_num}, Root:{root_rpid}. Total:{fetched_count}")

                                    if max_replies is not None and max_replies > 0 and fetched_count >= max_replies:
                                        logger.debug(f"Sub-comment limit {max_replies} met/exceeded: Root:{root_rpid}")
                                        return sub_comments

                                    cursor = data.get('data', {}).get('cursor', {})
                                    if cursor.get('is_end'):
                                        logger.debug(f"API indicates end (Sub): Root:{root_rpid}")
                                        return sub_comments

                                    last_exception = None
                                    break

                                elif api_code in [12002, -404]:
                                    logger.warning(
                                        f"Comments ended/closed (Sub): Root:{root_rpid}, Pg:{page_num}, Code:{api_code}")
                                    return sub_comments
                                else:
                                    logger.warning(
                                        f"API error (Sub): Pg:{page_num}, Root:{root_rpid}, Code:{api_code}, Msg:{data.get('message', 'N/A')}")
                                    last_exception = Exception(f"API Error {api_code}")
                                    should_retry = True
                            else:
                                logger.warning(f"Bad JSON (Sub): Pg:{page_num}, Root:{root_rpid}. Retrying...")
                                last_exception = Exception("Status 200 bad JSON")
                                should_retry = True

                            if should_retry and attempt < BILIBILI_COMMENTS_MAX_RETRIES - 1:
                                await asyncio.sleep(BILIBILI_COMMENTS_RETRY_DELAY_SECONDS * (
                                            attempt + 1) if response.status == 412 else BILIBILI_COMMENTS_RETRY_DELAY_SECONDS)
                                continue
                            elif should_retry:
                                logger.error(
                                    f"Failed fetch (Sub): Pg:{page_num}, Root:{root_rpid} after {BILIBILI_COMMENTS_MAX_RETRIES} attempts. Last: {last_exception}")
                                return sub_comments
                            else:
                                break

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(
                        f"Network/Timeout (Sub): Pg:{page_num}, Root:{root_rpid}, Att:{attempt + 1}, Error: {e}")
                    last_exception = e
                    if attempt < BILIBILI_COMMENTS_MAX_RETRIES - 1:
                        await asyncio.sleep(BILIBILI_COMMENTS_RETRY_DELAY_SECONDS * (attempt + 1))
                    else:
                        logger.error(
                            f"Failed fetch (Sub - Network/Timeout): Pg:{page_num}, Root:{root_rpid} after {BILIBILI_COMMENTS_MAX_RETRIES} attempts.")
                        return sub_comments
                except Exception as e:
                    logger.exception(
                        f"Unexpected error (Sub): Pg:{page_num}, Root:{root_rpid}, Att:{attempt + 1}, Error: {e}")
                    logger.error("Stopping fetch (Sub) due to unexpected error.")
                    return sub_comments
            if last_exception is not None:
                logger.error(f"Stopping fetch (Sub) for Root:{root_rpid} after page {page_num} failed permanently.")
                break
            page_num += 1
        return sub_comments

    async def get_comments(self, video_url):
        result = {'bvid': None, 'comments': [], 'error': None}
        bvid = self._extract_bvid_from_url(video_url)
        result['bvid'] = bvid
        if not bvid:
            result['error'] = "Failed to extract BVID from URL."
            logger.error(result['error'] + f" URL: {video_url}")
            return result

        if not self.cookies:
            result['error'] = "Cookies not loaded."
            return result

        main_comments_data = []
        tasks_for_sub_comments = []
        rpid_to_main_comment_index = {}

        async with aiohttp.ClientSession() as session:
            aid = await self._get_aid_from_bvid(session, bvid)
            if not aid:
                result['error'] = f"Failed to get AID for BVID {bvid}."
                logger.error(result['error'])
                return result

            max_main_comments = BILIBILI_COMMENTS_MAX_NUM
            max_replies = BILIBILI_COMMENTS_MAX_REPLIES_PER_COMMENT
            logger.info(
                f"Starting fetch: AID:{aid}, BVID:{bvid}, MaxMain:{max_main_comments or 'All'}, MaxReplies:{max_replies or 'All'}")

            page_num = 1
            max_pages = float('inf')
            if max_main_comments is not None and max_main_comments > 0:
                max_pages = (max_main_comments + BILIBILI_COMMENTS_COMMENT_PAGE_SIZE - 1) // BILIBILI_COMMENTS_COMMENT_PAGE_SIZE

            while True:
                if page_num > max_pages:
                    logger.debug(f"Reached max pages ({int(max_pages)}) for main comments.")
                    break

                page_data = await self._fetch_comment_page(session, aid, page_num)
                if page_data is None:
                    if page_num == 1 and not main_comments_data:
                        result['error'] = f"Failed initial comment fetch/Comments closed: AID {aid}."
                        logger.warning(result['error'])
                    else:
                        logger.debug(f"Stopping main fetch at page {page_num}.")
                    break

                replies = page_data.get('replies', [])
                if not replies:
                    logger.debug(f"No more main comments: Pg:{page_num}, AID:{aid}.")
                    break

                processed_main_replies = self._process_main_replies(replies)

                replies_to_add = processed_main_replies
                if max_main_comments is not None and max_main_comments > 0:
                    remaining_capacity = max_main_comments - len(main_comments_data)
                    if remaining_capacity <= 0:
                        logger.debug(f"Reached max main comments limit ({max_main_comments}).")
                        break
                    replies_to_add = processed_main_replies[:int(remaining_capacity)]

                for reply_data in replies_to_add:
                    current_index = len(main_comments_data)
                    main_comments_data.append(reply_data)
                    if reply_data['rcount'] > 0 and reply_data['rpid'] is not None:
                        rpid = reply_data['rpid']
                        task = asyncio.create_task(self._fetch_sub_comments(session, aid, rpid))
                        tasks_for_sub_comments.append(task)
                        rpid_to_main_comment_index[rpid] = current_index
                        logger.debug(f"Created sub-task: Root:{rpid}, Index:{current_index}")
                logger.debug(
                    f"Fetched main pg {page_num}. Added {len(replies_to_add)}. Total main: {len(main_comments_data)}")
                page_num += 1

            sub_comments_results = {}
            if tasks_for_sub_comments:
                logger.info(f"Fetching replies for {len(tasks_for_sub_comments)} main comments...")
                task_to_rpid = {task: rpid for rpid, task in
                                zip(rpid_to_main_comment_index.keys(), tasks_for_sub_comments)}
                gathered_results = await asyncio.gather(*tasks_for_sub_comments, return_exceptions=True)

                for i, task_result in enumerate(gathered_results):
                    task = tasks_for_sub_comments[i]
                    rpid = task_to_rpid.get(task)
                    if rpid is None: continue

                    if isinstance(task_result, Exception):
                        logger.error(f"Error fetching sub-comments: Root:{rpid}, Error:{task_result}")
                        sub_comments_results[rpid] = []
                    elif isinstance(task_result, list):
                        logger.debug(f"Fetched {len(task_result)} sub-comments: Root:{rpid}")
                        sub_comments_results[rpid] = task_result
                    else:
                        logger.warning(f"Unexpected result type (Sub): Root:{rpid}, Type:{type(task_result)}")
                        sub_comments_results[rpid] = []
            else:
                logger.info("No sub-comments to fetch.")

            final_comment_list = []
            for main_data in main_comments_data:
                main_message = main_data['message']
                rpid = main_data['rpid']
                fetched_sub_comments = sub_comments_results.get(rpid, []) if rpid is not None else []

                if len(main_message) < BILIBILI_COMMENTS_LENGTH_MIN:
                    continue

                full_comment = main_message
                if fetched_sub_comments:
                    reply_strings = [f" [回复] {sub}" for sub in fetched_sub_comments]
                    full_comment += "".join(reply_strings)

                final_comment_list.append(full_comment)

            result['comments'] = final_comment_list
            logger.info(f"Assembled {len(final_comment_list)} final comment strings.")

        logger.info(f"Processing finished: BVID {bvid}. Total comments: {len(result['comments'])}")
        return result


if __name__ == "__main__":
    import pprint

    test_url_with_comments = "https://www.bilibili.com/video/BV1gcoeY8Eq9"

    async def run_test(url, cookie_str):
        print(f"--- Starting test for URL: {url} ---")
        crawler = BilibiliCommentsCrawler(cookies=cookie_str)
        start_time = time.time()
        comment_info = await crawler.get_comments(url)
        end_time = time.time()
        print(f"Test finished in {end_time - start_time:.2f} seconds.")
        print("\n--- Test Result ---")
        pprint.pprint(comment_info)
        print("\n-------------------\n")

    from utils.cookies_tool import load_cookies
    cookies_to_test = load_cookies("Bilibili")

    async def main():
        await run_test(test_url_with_comments, cookies_to_test)

    asyncio.run(main())
