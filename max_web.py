import os
from pathlib import Path

from filelock import FileLock, Timeout
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(
    os.getenv("MAX_WEB_DATA_DIR", str(BASE_DIR.parent / "email-bridge-data"))
)
PROFILE_DIR = DATA_DIR / "max-profile"
SCREENSHOT_PATH = DATA_DIR / "max-login.png"
MAX_WEB_URL = "https://web.max.ru/"


def capture_login_screen():
    """Open MAX Web, persist the browser session and capture its current screen."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    lock = FileLock(str(PROFILE_DIR) + ".lock", timeout=15)
    try:
        with lock:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR),
                    headless=True,
                    args=["--no-sandbox"],
                    viewport={"width": 1280, "height": 900},
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(
                        MAX_WEB_URL,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    page.wait_for_timeout(8000)
                    page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
                    return {
                        "ok": True,
                        "url": page.url,
                        "title": page.title(),
                        "screenshot": str(SCREENSHOT_PATH),
                    }
                finally:
                    context.close()
    except Timeout:
        return {"ok": False, "error": "Браузер MAX занят другой операцией"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


def get_screenshot_path():
    return SCREENSHOT_PATH
