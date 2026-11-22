import logging
import os
import signal
import time
from datetime import datetime, timezone

from filelock import FileLock
from playwright.sync_api import sync_playwright

from max_web import (
    COMMAND_PATH,
    DATA_DIR,
    MAX_WEB_URL,
    PROFILE_DIR,
    SCREENSHOT_PATH,
    STATUS_PATH,
    _atomic_json_write,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("email_bridge.max_web")
_running = True


def stop_worker(*_):
    global _running
    _running = False


signal.signal(signal.SIGTERM, stop_worker)
signal.signal(signal.SIGINT, stop_worker)


def write_status(**values):
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **values,
    }
    _atomic_json_write(STATUS_PATH, payload)


def take_command():
    if not COMMAND_PATH.exists():
        return None
    with FileLock(str(COMMAND_PATH) + ".lock", timeout=2):
        if not COMMAND_PATH.exists():
            return None
        try:
            import json
            with COMMAND_PATH.open("r", encoding="utf-8") as stream:
                command = json.load(stream)
        except (OSError, ValueError):
            command = None
        try:
            COMMAND_PATH.unlink()
        except FileNotFoundError:
            pass
        return command


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    context = None
    page = None
    last_screenshot = 0.0

    log.info("MAX Web worker запущен; ожидаю команду из веб-панели")
    write_status(running=False, message="Ожидание запуска MAX Web", error=None)

    with sync_playwright() as playwright:
        while _running:
            command = take_command()
            if command and command.get("action") == "open":
                try:
                    if context is None:
                        context = playwright.chromium.launch_persistent_context(
                            user_data_dir=str(PROFILE_DIR),
                            headless=True,
                            args=["--no-sandbox"],
                            viewport={"width": 1280, "height": 900},
                        )
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(
                        MAX_WEB_URL,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    last_screenshot = 0.0
                    write_status(
                        running=True,
                        message="MAX Web открыт. QR-код должен появиться на снимке экрана.",
                        url=page.url,
                        title=page.title(),
                        error=None,
                    )
                    log.info("MAX Web открыт: %s", page.url)
                except Exception as exc:
                    log.exception("Не удалось открыть MAX Web")
                    write_status(
                        running=context is not None,
                        message="Не удалось открыть MAX Web",
                        error=str(exc)[:500],
                    )

            if context is not None and page is not None:
                try:
                    if page.is_closed():
                        page = context.new_page()
                        page.goto(MAX_WEB_URL, wait_until="domcontentloaded", timeout=60000)
                    now = time.monotonic()
                    if now - last_screenshot >= 3:
                        page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
                        last_screenshot = now
                        write_status(
                            running=True,
                            message="Браузер MAX работает. Состояние авторизации проверяется отдельно.",
                            url=page.url,
                            title=page.title(),
                            error=None,
                        )
                except Exception as exc:
                    log.warning("Ошибка обновления экрана MAX Web: %s", str(exc)[:300])
                    write_status(
                        running=False,
                        message="Потеряно соединение с окном MAX Web. Нажмите «Открыть / обновить QR».",
                        error=str(exc)[:500],
                    )
                    try:
                        context.close()
                    except Exception:
                        pass
                    context = None
                    page = None

            time.sleep(1)

    if context is not None:
        try:
            context.close()
        except Exception:
            pass
    write_status(running=False, message="MAX Web worker остановлен", error=None)
    log.info("MAX Web worker остановлен")


if __name__ == "__main__":
    main()
