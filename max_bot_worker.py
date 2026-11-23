import json
import logging
import os
import signal
import time
from pathlib import Path

import requests

from app import create_app
from models import db, Settings, Rule
from notifications import send_max_text

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("email_bridge.max_bot")
API = "https://platform-api2.max.ru"
MARKER_FILE = Path(__file__).with_name(".max_updates_marker")
_running = True


def stop_worker(*_):
    global _running
    _running = False


signal.signal(signal.SIGTERM, stop_worker)
signal.signal(signal.SIGINT, stop_worker)


def load_marker():
    try:
        data = json.loads(MARKER_FILE.read_text(encoding="utf-8"))
        return data.get("marker")
    except (OSError, ValueError, TypeError):
        return None


def save_marker(marker):
    if marker is None:
        return
    temporary = MARKER_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"marker": marker}), encoding="utf-8")
    temporary.replace(MARKER_FILE)


def get_reply(text, app):
    command = (text or "").strip()
    first = command.split(maxsplit=1)[0].lower() if command else ""
    first = first.split("@", 1)[0]

    if first in {"/start", "/help", "/commands"}:
        return (
            "Здравствуйте! Я бот «Заявки» — помощник Email Bridge.\n\n"
            "Команды:\n"
            "/start — приветствие\n"
            "/help — список команд\n"
            "/status — состояние настроек почты и правил\n\n"
            "Я принимаю сообщения и отправляю уведомления о письмах по заданным правилам."
        )

    if first == "/status":
        with app.app_context():
            settings = Settings.query.first()
            rules_count = Rule.query.filter_by(enabled=True).count()
            mail_ready = bool(
                settings and settings.imap_server and settings.email_user and settings.email_pass
            )
            max_ready = bool(settings and settings.max_token)
        return (
            "Состояние Email Bridge:\n"
            f"Почта: {'настроена' if mail_ready else 'не настроена'}\n"
            f"Токен MAX: {'сохранён' if max_ready else 'не задан'}\n"
            f"Включённых правил: {rules_count}\n"
            "Проверка почты выполняется отдельной службой."
        )

    if command:
        return (
            "Сообщение получил. Бот работает.\n"
            "Для списка команд отправьте /help."
        )
    return "Бот работает. Отправьте /help, чтобы увидеть команды."


def handle_update(update, token, app):
    update_type = update.get("update_type")

    if update_type == "bot_started":
        user = update.get("user") or {}
        message = get_reply("/start", app)
    elif update_type == "message_created":
        message_obj = update.get("message") or {}
        body = message_obj.get("body") or {}
        message = get_reply(body.get("text") or "", app)
        user = update.get("user") or message_obj.get("sender") or {}
        if user.get("is_bot"):
            return
    else:
        return

    user_id = user.get("user_id") or update.get("user_id")
    chat_id = update.get("chat_id")
    if not chat_id:
        recipient = message_obj.get("recipient") if update_type == "message_created" else {}
        chat_id = (recipient or {}).get("chat_id")

    if user_id:
        recipient_id = f"user:{user_id}"
    elif chat_id:
        recipient_id = f"chat:{chat_id}"
    else:
        log.warning("MAX: событие без user_id и chat_id: %s", update_type)
        return

    send_max_text(token, recipient_id, message)
    log.info("Ответ MAX отправлен: событие=%s получатель=%s", update_type, recipient_id)


def main():
    app = create_app()
    marker = load_marker()
    log.info("MAX Bot worker запущен; сохранённый marker: %s", marker)

    while _running:
        try:
            with app.app_context():
                settings = Settings.query.first()
                token = settings.max_token if settings else ""
            if not token:
                log.warning("MAX Bot: токен не задан; повтор через 30 секунд")
                time.sleep(30)
                continue

            params = {"limit": 100, "timeout": 30}
            if marker is not None:
                params["marker"] = marker
            response = requests.get(
                f"{API}/updates",
                params=params,
                headers={"Authorization": token},
                timeout=40,
            )
            if not response.ok:
                try:
                    detail = response.json()
                except ValueError:
                    detail = response.text[:500]
                raise RuntimeError(f"MAX updates HTTP {response.status_code}: {detail}")

            payload = response.json()
            updates = payload.get("updates") or []
            next_marker = payload.get("marker")

            for update in updates:
                try:
                    handle_update(update, token, app)
                except Exception:
                    log.exception(
                        "Ошибка обработки события MAX: %s",
                        update.get("update_type", "unknown"),
                    )

            if next_marker is not None:
                marker = next_marker
                save_marker(marker)

            if updates:
                log.info("MAX: получено событий: %s", len(updates))

        except requests.RequestException as exc:
            log.warning("Сетевая ошибка MAX Long Polling: %s", exc)
            time.sleep(5)
        except Exception:
            log.exception("Ошибка MAX Bot worker")
            time.sleep(10)

    log.info("MAX Bot worker остановлен")


if __name__ == "__main__":
    main()
