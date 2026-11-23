from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    imap_server = db.Column(db.String(255), nullable=False, default="imap.yandex.ru")
    imap_port = db.Column(db.Integer, nullable=False, default=993)
    email_user = db.Column(db.String(255), nullable=False, default="")
    email_pass = db.Column(db.String(1000), nullable=False, default="")
    tg_token = db.Column(db.String(1000), nullable=False, default="")
    max_token = db.Column(db.String(1000), nullable=False, default="")
    check_interval = db.Column(db.Integer, nullable=False, default=60)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Rule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(200), nullable=False)
    target = db.Column(db.String(10), nullable=False)  # TG or MAX
    recipient_id = db.Column(db.String(100), nullable=False)  # MAX: chat ID or user:ID
    enabled = db.Column(db.Boolean, nullable=False, default=True)

class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    channel = db.Column(db.String(10), nullable=False)  # TG or MAX
    recipient_id = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ProcessedMail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(255), unique=True, nullable=False)
    processed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    result = db.Column(db.String(30), nullable=False, default="sent")
    details = db.Column(db.Text, nullable=False, default="")

class DeliveryLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(255), nullable=False, index=True)
    subject = db.Column(db.String(500), nullable=False, default="(без темы)")
    destination = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    details = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
