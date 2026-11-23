import html
import logging

import requests

import proxy_manager

log = logging.getLogger("email_bridge.notifications")


class DeliveryError(RuntimeError):
    pass


def _response_error(response):
    try:
        payload = response.json()
        return str(payload.get("description") or payload.get("message") or payload)
    except Exception:
        return response.text[:500]


def send_telegram(token: str, chat_id: str, subject: str, sender: str, body: str) -> None:
    if not token:
        raise DeliveryError("Не задан токен Telegram")

    safe_subject = html.escape(subject or "(без темы)")
    safe_sender = html.escape(sender or "не указан")
    safe_body = html.escape((body or "Письмо не содержит текстовой части")[:3000])
    message = f"📧 <b>{safe_subject}</b>\nОт: {safe_sender}\n\n{safe_body}"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    last_error = "Нет рабочих SOCKS5-прокси"
    for attempt in range(5):
        proxies = proxy_manager.get_proxy()
        if not proxies:
            raise DeliveryError(f"Telegram: {last_error}")

        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                proxies=proxies,
                timeout=20,
            )
        except requests.RequestException as exc:
            last_error = str(exc).replace(token, "[REDACTED]")
            proxy_manager.mark_failed()
            log.warning(
                "Сетевая ошибка SOCKS5, попытка %s/5: %s",
                attempt + 1,
                last_error,
            )
            continue

        try:
            result = response.json()
        except ValueError:
            result = {}

        if not response.ok or not result.get("ok", False):
            raise DeliveryError(
                f"Telegram: HTTP {response.status_code}: {_response_error(response)}"
            )
        return

    raise DeliveryError(f"Telegram: не удалось отправить через SOCKS5: {last_error}")


def send_max_text(token: str, recipient: str, message: str) -> dict:
    """Send plain text to MAX. recipient: user:ID, chat:ID, or plain chat ID."""
    if not token:
        raise DeliveryError("Не задан токен MAX")

    recipient = str(recipient).strip()
    if recipient.startswith("user:"):
        params = {"user_id": recipient.split(":", 1)[1]}
    elif recipient.startswith("chat:"):
        params = {"chat_id": recipient.split(":", 1)[1]}
    else:
        params = {"chat_id": recipient}

    response = requests.post(
        "https://platform-api2.max.ru/messages",
        params=params,
        headers={"Authorization": token, "Content-Type": "application/json"},
        json={"text": (message or "")[:4000]},
        timeout=25,
    )
    if not response.ok:
        raise DeliveryError(f"MAX: HTTP {response.status_code}: {_response_error(response)}")
    try:
        result = response.json()
    except ValueError:
        result = {}
    if not result.get("message"):
        raise DeliveryError(f"MAX: API не вернул объект message: {result}")
    return result


def send_max(token: str, recipient: str, subject: str, sender: str, body: str) -> None:
    text = (
        f"📧 {subject or '(без темы)'}\nОт: {sender or 'не указан'}\n\n"
        f"{(body or 'Письмо не содержит текстовой части')[:3500]}"
    )
    send_max_text(token, recipient, text)
