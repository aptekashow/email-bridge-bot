"""SOCKS5 proxy pool for Telegram Bot API.

The list source is a public proxy list. Proxies are untrusted: use them only
for Telegram API traffic. Do not send mail credentials or MAX traffic through
this pool.
"""
import ipaddress
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

log = logging.getLogger("email_bridge.proxy")

SOURCE = "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"
TEST_URL = "https://api.telegram.org/"
REFRESH_INTERVAL = 2 * 60 * 60
MAX_CANDIDATES = 200
MAX_WORKERS = 40
CACHE_FILE = Path(__file__).with_name("proxies_cache.txt")

_lock = threading.Lock()
_refresh_lock = threading.Lock()
_proxies = []
_started = False
_thread = None


def _valid_proxy(value):
    match = re.fullmatch(r"([^:\s]+):(\d{1,5})", value.strip())
    if not match:
        return False
    host, port = match.groups()
    try:
        ipaddress.ip_address(host)
        return 1 <= int(port) <= 65535
    except ValueError:
        return False


def _load_cache():
    try:
        cached = [
            line.strip()
            for line in CACHE_FILE.read_text(encoding="utf-8").splitlines()
            if _valid_proxy(line.strip())
        ]
        if cached:
            with _lock:
                _proxies[:] = list(dict.fromkeys(cached))
            log.info("Из кеша загружено SOCKS5-прокси: %s", len(cached))
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("Не удалось прочитать кеш SOCKS5-прокси")


def _check_proxy(proxy):
    proxy_url = f"socks5h://{proxy}"
    started_at = time.monotonic()
    try:
        # Telegram's root may return a redirect or client error; both prove
        # that the HTTPS request reached Telegram through this proxy.
        response = requests.get(
            TEST_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=(4, 7),
            allow_redirects=False,
        )
        if response.status_code < 500:
            return proxy, time.monotonic() - started_at
    except requests.RequestException:
        pass
    return None


def refresh():
    global _proxies

    if not _refresh_lock.acquire(blocking=False):
        return

    try:
        try:
            response = requests.get(SOURCE, timeout=20)
            response.raise_for_status()
            candidates = []
            for line in response.text.splitlines():
                value = line.strip()
                if _valid_proxy(value):
                    candidates.append(value)
            candidates = list(dict.fromkeys(candidates))[:MAX_CANDIDATES]
            log.info("Загружено прокси: %s", len(candidates))
        except Exception:
            log.exception("Не удалось загрузить список прокси")
            return

        working = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(_check_proxy, item) for item in candidates]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        working.append(result)
                except Exception:
                    log.exception("Ошибка проверки SOCKS5-прокси")

        working.sort(key=lambda item: item[1])

        if not working:
            log.error("Рабочих SOCKS5-прокси не найдено; прежний список сохранён")
            return

        new_list = [proxy for proxy, _latency in working]
        with _lock:
            _proxies[:] = new_list

        try:
            CACHE_FILE.write_text("\n".join(new_list) + "\n", encoding="utf-8")
        except OSError:
            log.exception("Не удалось сохранить кеш SOCKS5-прокси")

        log.info(
            "Рабочих прокси: %s. Самый быстрый: %s (%.2f сек.)",
            len(working), working[0][0], working[0][1]
        )
    finally:
        _refresh_lock.release()


def get_proxy():
    with _lock:
        if not _proxies:
            return None
        selected = _proxies[0]
    proxy_url = f"socks5h://{selected}"
    return {"http": proxy_url, "https": proxy_url}


def mark_failed():
    with _lock:
        if not _proxies:
            return False
        failed = _proxies.pop(0)
    log.warning("Прокси исключён после сетевой ошибки: %s", failed)
    return True


def _refresh_loop():
    while True:
        time.sleep(REFRESH_INTERVAL)
        refresh()


def start():
    global _started, _thread
    with _lock:
        if _started:
            return
        _started = True

    _load_cache()
    refresh()

    _thread = threading.Thread(
        target=_refresh_loop,
        name="proxy-refresh",
        daemon=True,
    )
    _thread.start()
    log.info("Автоматическое обновление SOCKS5 запущено (раз в 2 часа)")
