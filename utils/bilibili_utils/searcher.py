from urllib.parse import quote

from playwright.async_api import async_playwright
from fake_useragent import UserAgent

from configs.logger_config import get_logger
from configs.utils_config import BILIBILI_SEARCH_RESULTS_NUM

logger = get_logger("utils.bilibili_utils.searcher")


class BilibiliSearcher:
    def __init__(self, cookies=None):
        self.ua = UserAgent(platforms="desktop").random
        self.cookies = cookies

    async def search(self, query):
        search_url = f"https://search.bilibili.com/all?keyword={quote(query)}"
        logger.info(f"Starting Bilibili search for '{query}'")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.ua)

            try:
                if self.cookies:
                    await context.add_cookies(self.cookies)
                    logger.debug("Cookies added to context")

                page = await context.new_page()
                await page.goto(search_url, timeout=15000)
                await page.wait_for_selector(".bili-video-card__info--right", timeout=10000)
                await page.wait_for_timeout(1000)

                links = await self._extract_video_links(page)
            except Exception as e:
                logger.exception(f"Bilibili search failed: {e}")
                links = []
            finally:
                await browser.close()

        logger.info(f"Found {len(links)} video(s) for '{query}'")
        return links

    async def _extract_video_links(self, page):
        elements = await page.query_selector_all(".bili-video-card__info--right a")
        links = []
        for elem in elements:
            href = await elem.get_attribute("href")
            if href and href.startswith("//www.bilibili.com/video/"):
                full_url = "https:" + href
                links.append(full_url)
            if len(links) >= BILIBILI_SEARCH_RESULTS_NUM:
                break
        return links


if __name__ == "__main__":
    import asyncio
    from utils.cookies_tool import load_cookies
    test_query = "魔兽世界11.1 戒律牧 天赋"
    cookies = load_cookies("Bilibili")
    bs = BilibiliSearcher(cookies=cookies)
    res = asyncio.run(bs.search(test_query))
    print(res)
