import logging
import os
import signal
import time
from email.utils import parsedate_to_datetime

from imap_tools import MailBox, AND
from sqlalchemy.exc import IntegrityError

from app import create_app
from models import db, Settings, Rule, ProcessedMail, DeliveryLog
from notifications import send_telegram, send_max

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("email_bridge.worker")
_running = True

def stop_worker(*_):
    global _running
    _running = False

signal.signal(signal.SIGTERM, stop_worker)
signal.signal(signal.SIGINT, stop_worker)

def stable_message_key(mailbox_name, msg):
    # IMAP UID is stable inside a mailbox; include mailbox/account to avoid collisions.
    uid = getattr(msg, "uid", None)
    message_id = (msg.headers.get("message-id") or [""])[0]
    return f"{mailbox_name}:{uid or message_id or msg.date or msg.subject}"

def process_mailbox_once():
    app = create_app()
    with app.app_context():
        settings = Settings.query.first()
        if not settings or not settings.email_user or not settings.email_pass:
            log.info("Настройки почты не заполнены")
            return
        rules = Rule.query.filter_by(enabled=True).all()
        if not rules:
            log.info("Нет включённых правил")
            return
        mailbox_name = f"{settings.email_user}:INBOX"
        try:
            with MailBox(settings.imap_server, port=settings.imap_port).login(
                settings.email_user, settings.email_pass, "INBOX"
            ) as mailbox:
                for msg in mailbox.fetch(AND(seen=False), mark_seen=False):
                    key = stable_message_key(mailbox_name, msg)
                    if ProcessedMail.query.filter_by(message_id=key).first():
                        mailbox.flag(msg.uid, "\\Seen", True)
                        continue
                    subject = msg.subject or "(без темы)"
                    body = (msg.text or "").strip()
                    if not body and msg.html:
                        # Keep this fallback plain and safe; no remote HTML is rendered by the panel.
                        import re
                        body = re.sub(r"<[^>]+>", " ", msg.html)
                    haystack = f"{subject}\n{body}".casefold()
                    matching = [r for r in rules if r.keyword.casefold() in haystack]
                    errors = []
                    successes = 0
                    for rule in matching:
                        try:
                            if rule.target == "TG":
                                send_telegram(settings.tg_token, rule.recipient_id, subject, msg.from_, body)
                                destination = f"Telegram: {rule.recipient_id}"
                            elif rule.target == "MAX":
                                send_max(settings.max_token, rule.recipient_id, subject, msg.from_, body)
                                destination = f"MAX: {rule.recipient_id}"
                            else:
                                raise RuntimeError(f"Неизвестный канал: {rule.target}")
                            db.session.add(DeliveryLog(message_id=key, subject=subject[:500], destination=destination, status="sent"))
                            successes += 1
                        except Exception as exc:
                            errors.append(str(exc))
                            db.session.add(DeliveryLog(message_id=key, subject=subject[:500], destination=f"{rule.target}: {rule.recipient_id}", status="error", details=str(exc)[:2000]))
                    # Do not mark failed deliveries as processed: the next poll retries them.
                    if errors:
                        db.session.commit()
                        log.error("Не удалось доставить письмо %s: %s", subject, "; ".join(errors))
                        continue
                    db.session.add(ProcessedMail(message_id=key, result="sent" if matching else "no_match", details=f"Успешных доставок: {successes}"))
                    db.session.commit()
                    mailbox.flag(msg.uid, "\\Seen", True)
                    if matching:
                        log.info("Письмо обработано: %s; доставок: %s", subject, successes)
        except Exception:
            db.session.rollback()
            log.exception("Ошибка подключения или обработки IMAP")

def main():
    app = create_app()
    with app.app_context():
        db.create_all()
    log.info("Email Bridge worker запущен")
    while _running:
        with app.app_context():
            settings = Settings.query.first()
            interval = max(15, min(3600, settings.check_interval if settings else 60))
        process_mailbox_once()
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)
    log.info("Worker остановлен")

if __name__ == "__main__":
    main()
