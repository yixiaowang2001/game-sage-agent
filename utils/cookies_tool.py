import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright
from configs.logger_config import get_logger

logger = get_logger("cookies_tool")
COOKIES_INFO = {
    "Bing": {
        "site_domain": ".bing.com",
        "site_url": "https://www.bing.com/",
        "cookie_path": "cookies/bing_cookies.json",
        "timeout": 60000
    },
    "NGA": {
        "site_domain": ".nga.cn",
        "site_url": "https://nga.178.com/",
        "cookie_path": "cookies/nga_cookies.json",
        "timeout": 120000
    },
    "BaiduTieba": {
        "site_domain": ".baidu.com",
        "site_url": "https://tieba.baidu.com/",
        "cookie_path": "cookies/tieba_cookies.json",
        "timeout": 120000
    },
    "Bilibili": {
        "site_domain": ".bilibili.com",
        "site_url": "https://www.bilibili.com/",
        "cookie_path": "cookies/bilibili_cookies.json",
        "timeout": 60000
    }
}


def save_cookies(site_name):
    site_info = COOKIES_INFO[site_name]
    site_domain = site_info["site_domain"]
    site_url = site_info["site_url"]
    cookie_path = site_info["cookie_path"]
    timeout = site_info["timeout"]

    file_path = Path(__file__).parents[1] / cookie_path
    os.makedirs(file_path.parent, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        logger.info(f"Opening {site_name}, please login manually and press Enter when done")
        try:
            page.goto(site_url, timeout=timeout)
            page.wait_for_load_state('networkidle')

            input("Press Enter after login is complete...")

            cookies = context.cookies()

            for cookie in cookies:
                cookie["domain"] = site_domain

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2, ensure_ascii=False)

            logger.info(f"{site_name} cookies saved to: {cookie_path}")
        except Exception as e:
            logger.error(f"Error saving {site_name} cookies: {str(e)}")
        finally:
            browser.close()


def _load_cookies_from_file(site_name):
    cookie_path = COOKIES_INFO[site_name]["cookie_path"]
    file_path = Path(__file__).parents[1] / cookie_path

    if not file_path.exists():
        logger.info(f"Cookie file {cookie_path} not found, using anonymous access")
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        return cookies
    except Exception as e:
        logger.warning(f"Failed to load {site_name} cookies from file. Error: {e}")
        return None


def load_cookies(site_name, context="sync"):
    cookies = _load_cookies_from_file(site_name)
    if not cookies:
        logger.info(f"Using anonymous access for {site_name} (no cookies found)")
        return None
    logger.info(f"Loaded cookies for {site_name} ({context})")
    return cookies


async def async_load_cookies(site_name):
    return load_cookies(site_name, context="async")


if __name__ == "__main__":
    save_cookies("Bilibili")
