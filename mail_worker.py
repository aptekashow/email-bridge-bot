import logging
import os
import signal
import time
from email.utils import parseaddr

from imap_tools import MailBox, AND

from app import create_app
from models import db, Settings, Rule, AllowedSender, ExcludedSender, ProcessedMail, DeliveryLog
from notifications import send_telegram, send_max
import proxy_manager

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
        allowed_senders = {
            item.email.casefold()
            for item in AllowedSender.query.all()
        }
        excluded_senders = {
            item.email.casefold()
            for item in ExcludedSender.query.all()
        }
        if not rules and not excluded_senders:
            log.info("Нет включённых правил и исключений")
            return

        mailbox_name = f"{settings.email_user}:INBOX"
        try:
            with MailBox(settings.imap_server, port=settings.imap_port).login(
                settings.email_user, settings.email_pass, "INBOX"
            ) as mailbox:
                for msg in mailbox.fetch(AND(seen=False), mark_seen=False):
                    sender_email = (parseaddr(msg.from_ or "")[1] or msg.from_ or "").strip().casefold()
                    if sender_email in excluded_senders:
                        mailbox.flag(msg.uid, "\\Seen", True)
                        log.info("Письмо от исключённого отправителя пропущено: %s", sender_email)
                        continue

                    if allowed_senders and sender_email not in allowed_senders:
                        mailbox.flag(msg.uid, "\\Seen", True)
                        log.info("Отправитель не входит в список проверки, письмо пропущено: %s", sender_email)
                        continue

                    key = stable_message_key(mailbox_name, msg)
                    processed = ProcessedMail.query.filter_by(message_id=key).first()
                    if processed:
                        if processed.result == "no_match":
                            # Legacy record from the old behavior: remove it and
                            # re-evaluate the unread message against current rules.
                            db.session.delete(processed)
                            db.session.commit()
                        else:
                            mailbox.flag(msg.uid, "\\Seen", True)
                            continue

                    subject = msg.subject or "(без темы)"
                    body = (msg.text or "").strip()
                    if not body and msg.html:
                        import re
                        body = re.sub(r"<[^>]+>", " ", msg.html)

                    # Intentionally search the body only. Subject is not included.
                    haystack = body.casefold()
                    best_rule = None
                    best_match_count = 1
                    for rule in rules:
                        # A rule qualifies when at least two distinct keywords
                        # from its comma-separated list occur in the body.
                        keywords = {
                            part.strip().casefold()
                            for part in rule.keyword.split(",")
                            if part.strip()
                        }
                        match_count = sum(keyword in haystack for keyword in keywords)
                        # Keep only the rule with the greatest number of matches.
                        # On a tie, the first rule returned by the database wins.
                        if match_count >= 2 and match_count > best_match_count:
                            best_rule = rule
                            best_match_count = match_count

                    matching = [best_rule] if best_rule is not None else []
                    errors = []
                    successes = 0

                    for rule in matching:
                        try:
                            if rule.target == "TG":
                                send_telegram(
                                    settings.tg_token, rule.recipient_id,
                                    subject, msg.from_, body
                                )
                                destination = f"Telegram: {rule.recipient_id}"
                            elif rule.target == "MAX":
                                send_max(
                                    settings.max_token, rule.recipient_id,
                                    subject, msg.from_, body
                                )
                                destination = f"MAX: {rule.recipient_id}"
                            else:
                                raise RuntimeError(f"Неизвестный канал: {rule.target}")

                            db.session.add(DeliveryLog(
                                message_id=key,
                                subject=subject[:500],
                                destination=destination,
                                status="sent",
                            ))
                            successes += 1
                        except Exception as exc:
                            errors.append(str(exc))
                            db.session.add(DeliveryLog(
                                message_id=key,
                                subject=subject[:500],
                                destination=f"{rule.target}: {rule.recipient_id}",
                                status="error",
                                details=str(exc)[:2000],
                            ))

                    if errors:
                        db.session.commit()
                        log.error(
                            "Не удалось доставить письмо %s: %s",
                            subject,
                            "; ".join(errors),
                        )
                        continue

                    if matching:
                        db.session.add(ProcessedMail(
                            message_id=key,
                            result="sent",
                            details=f"Успешных доставок: {successes}",
                        ))
                        db.session.commit()
                        mailbox.flag(msg.uid, "\\Seen", True)
                        log.info(
                            "Письмо обработано: %s; доставок: %s",
                            subject,
                            successes,
                        )
                    else:
                        # No rule matched: do not mark the message as seen and
                        # do not persist a processed marker. It stays unread and
                        # can be evaluated again after rules are changed.
                        db.session.commit()
                        log.info(
                            "Нет двух совпавших ключевых слов ни в одном правиле: %s; письмо оставлено непрочитанным",
                            subject,
                        )
        except Exception:
            db.session.rollback()
            log.exception("Ошибка подключения или обработки IMAP")


def main():
    app = create_app()
    with app.app_context():
        db.create_all()

    # Loads the saved proxy cache, refreshes the public list, then refreshes
    # it every two hours in the background.
    proxy_manager.start()
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
