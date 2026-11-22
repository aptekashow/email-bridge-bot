import logging
import os
import signal
import subprocess
import time
from datetime import datetime, timezone

from filelock import FileLock, Timeout
from playwright.sync_api import sync_playwright

from max_web import (
    COMMAND_PATH, DATA_DIR, DIAGNOSTICS_PATH, MAX_WEB_URL,
    PROFILE_DIR, STATUS_PATH, VNC_AUTH_PATH, _atomic_json_write,
    get_vnc_password,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("email_bridge.max_web")
_running = True
_display_processes = []


def stop_worker(*_):
    global _running
    _running = False


signal.signal(signal.SIGTERM, stop_worker)
signal.signal(signal.SIGINT, stop_worker)


def write_status(**values):
    previous = {}
    try:
        import json
        with STATUS_PATH.open("r", encoding="utf-8") as stream:
            loaded = json.load(stream)
        if isinstance(loaded, dict):
            previous = loaded
    except (OSError, ValueError):
        pass
    payload = {**previous, "updated_at": datetime.now(timezone.utc).isoformat(), **values}
    _atomic_json_write(STATUS_PATH, payload)


def take_command():
    if not COMMAND_PATH.exists():
        return None
    try:
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
    except Timeout:
        return None


def start_display():
    """Start a private X display, password-protected VNC and noVNC websocket gateway."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    password = get_vnc_password()
    os.chmod(DATA_DIR, 0o700)
    VNC_AUTH_PATH.unlink(missing_ok=True)
    subprocess.run(
        ["x11vnc", "-storepasswd", password, str(VNC_AUTH_PATH)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    os.chmod(VNC_AUTH_PATH, 0o600)

    env = os.environ.copy()
    env["DISPLAY"] = ":99"
    commands = [
        ["Xvfb", ":99", "-screen", "0", "1280x900x24", "-nolisten", "tcp", "-ac"],
        ["x11vnc", "-display", ":99", "-rfbauth", str(VNC_AUTH_PATH),
         "-forever", "-shared", "-rfbport", "5900", "-localhost", "-noxdamage"],
        ["websockify", "--web=/usr/share/novnc", "0.0.0.0:6080", "localhost:5900"],
    ]
    for command in commands:
        process = subprocess.Popen(
            command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        _display_processes.append(process)
        time.sleep(0.7)
        if process.poll() is not None:
            raise RuntimeError(f"Не запустилась служба экрана: {command[0]}")
    os.environ["DISPLAY"] = ":99"
    log.info("Удалённый экран запущен: noVNC TCP 6080; VNC защищён паролем")


def stop_display():
    for process in reversed(_display_processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(_display_processes):
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    _display_processes.clear()


def inspect_page(page):
    """Collect page state and UI control labels, never chat message contents."""
    controls = page.locator("button, a, input, textarea, [role='button'], [role='textbox']")
    items = []
    count = min(controls.count(), 250)
    for index in range(count):
        element = controls.nth(index)
        try:
            if not element.is_visible():
                continue
            item = element.evaluate("""el => ({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                aria: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                placeholder: el.getAttribute('placeholder') || ''
            })""")
            if any(item.values()):
                items.append(item)
        except Exception:
            continue

    body_text = page.locator("body").inner_text(timeout=5000)
    lower_text = body_text.casefold()
    login_indicators = ("войти", "qr-код", "qr code", "сканируйте", "вход по")
    authenticated = not any(marker in lower_text for marker in login_indicators)
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "url": page.url,
        "title": page.title(),
        "authenticated_guess": authenticated,
        "controls": items,
    }
    _atomic_json_write(DIAGNOSTICS_PATH, payload)
    write_status(
        running=True,
        message="Интерфейс MAX проверен. Статус входа предварительный — требуется визуальная проверка.",
        url=page.url, title=page.title(), authenticated_guess=authenticated, error=None,
    )
    log.info("MAX Web UI inspection completed: %s visible controls", len(items))


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    os.chmod(PROFILE_DIR, 0o700)
    context = None
    page = None

    log.info("MAX Web worker запускает интерактивный экран")
    write_status(running=False, message="Запуск интерактивного браузера MAX", error=None)
    try:
        start_display()
        with sync_playwright() as playwright:
            while _running:
                command = take_command()
                if command and command.get("action") in {"open", "inspect"}:
                    try:
                        if context is None:
                            context = playwright.chromium.launch_persistent_context(
                                user_data_dir=str(PROFILE_DIR),
                                headless=False,
                                args=["--no-sandbox", "--disable-dev-shm-usage"],
                                viewport={"width": 1280, "height": 900},
                            )
                        page = context.pages[0] if context.pages else context.new_page()
                        if command.get("action") == "open":
                            page.goto(MAX_WEB_URL, wait_until="domcontentloaded", timeout=60000)
                            write_status(
                                running=True,
                                message="MAX Web открыт в интерактивном окне. Подключитесь к экрану ниже.",
                                url=page.url, title=page.title(), authenticated_guess=None, error=None,
                            )
                            log.info("MAX Web открыт: %s", page.url)
                        else:
                            if not page.url or page.url == "about:blank":
                                page.goto(MAX_WEB_URL, wait_until="domcontentloaded", timeout=60000)
                            inspect_page(page)
                    except Exception as exc:
                        log.exception("Ошибка операции MAX Web")
                        if context is not None:
                            try:
                                context.close()
                            except Exception:
                                pass
                        context = None
                        page = None
                        write_status(
                            running=False,
                            message="Не удалось выполнить операцию MAX Web. Проверьте журнал службы.",
                            error=str(exc)[:500],
                        )
                if context is not None and page is not None:
                    try:
                        if page.is_closed():
                            page = context.new_page()
                            page.goto(MAX_WEB_URL, wait_until="domcontentloaded", timeout=60000)
                    except Exception as exc:
                        log.warning("Потеряно окно MAX Web: %s", str(exc)[:300])
                        write_status(running=False, message="Окно MAX Web закрыто", error=str(exc)[:500])
                        try:
                            context.close()
                        except Exception:
                            pass
                        context = None
                        page = None
                time.sleep(1)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        stop_display()
        write_status(running=False, message="MAX Web worker остановлен", error=None)
        log.info("MAX Web worker остановлен")


if __name__ == "__main__":
    main()
