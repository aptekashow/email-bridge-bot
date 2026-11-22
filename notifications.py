import html
import logging
import requests

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
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=20,
    )
    if not response.ok or not response.json().get("ok", False):
        raise DeliveryError(f"Telegram: HTTP {response.status_code}: {_response_error(response)}")

def send_max(token: str, recipient: str, subject: str, sender: str, body: str) -> None:
    """MAX Bot API. recipient may be 'user:123'; plain ID is treated as chat_id."""
    if not token:
        raise DeliveryError("Не задан токен MAX")
    recipient = recipient.strip()
    if recipient.startswith("user:"):
        params = {"user_id": recipient.split(":", 1)[1]}
    elif recipient.startswith("chat:"):
        params = {"chat_id": recipient.split(":", 1)[1]}
    else:
        params = {"chat_id": recipient}
    text = f"📧 {subject or '(без темы)'}\nОт: {sender or 'не указан'}\n\n{(body or 'Письмо не содержит текстовой части')[:3500]}"
    response = requests.post(
        "https://platform-api2.max.ru/messages",
        params=params,
        headers={"Authorization": token, "Content-Type": "application/json"},
        json={"text": text},
        timeout=20,
    )
    if not response.ok:
        raise DeliveryError(f"MAX: HTTP {response.status_code}: {_response_error(response)}")
