import os
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import inspect, text
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

from config import Config
from models import db, Settings, Rule, DeliveryLog, ProcessedMail

csrf = CSRFProtect()

def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        db.create_all()
        # Lightweight SQLite-compatible migration from the initial prototype schema.
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        if "rule" in tables:
            rule_columns = {column["name"] for column in inspector.get_columns("rule")}
            if "enabled" not in rule_columns:
                db.session.execute(text("ALTER TABLE rule ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1"))
        if "processed_mail" in tables:
            mail_columns = {column["name"] for column in inspector.get_columns("processed_mail")}
            if "result" not in mail_columns:
                db.session.execute(text("ALTER TABLE processed_mail ADD COLUMN result VARCHAR(30) NOT NULL DEFAULT 'sent'"))
            if "details" not in mail_columns:
                db.session.execute(text("ALTER TABLE processed_mail ADD COLUMN details TEXT NOT NULL DEFAULT ''"))
        db.session.commit()
        if not Settings.query.first():
            db.session.add(Settings())
            db.session.commit()

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = os.getenv("ADMIN_PASSWORD", "")
            supplied = request.form.get("password", "")
            if password and supplied == password:
                session.clear()
                session["authenticated"] = True
                next_url = request.args.get("next", "")
                if next_url.startswith("/") and not next_url.startswith("//"):
                    return redirect(next_url)
                return redirect(url_for("index"))
            flash("Неверный пароль или не задан ADMIN_PASSWORD", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        return render_template(
            "index.html",
            rules_count=Rule.query.filter_by(enabled=True).count(),
            processed_count=ProcessedMail.query.count(),
            recent=DeliveryLog.query.order_by(DeliveryLog.created_at.desc()).limit(8).all(),
        )

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        s = Settings.query.first()
        if request.method == "POST":
            try:
                s.imap_server = request.form.get("imap_server", "").strip()
                s.imap_port = int(request.form.get("imap_port", "993"))
                s.email_user = request.form.get("email_user", "").strip()
                # Empty secret fields mean "leave existing value unchanged".
                for field in ("email_pass", "tg_token", "max_token"):
                    value = request.form.get(field, "").strip()
                    if value:
                        setattr(s, field, value)
                s.check_interval = int(request.form.get("check_interval", "60"))
                if not s.imap_server or not (1 <= s.imap_port <= 65535):
                    raise ValueError("Проверьте IMAP-сервер и порт")
                if not (15 <= s.check_interval <= 3600):
                    raise ValueError("Интервал должен быть от 15 до 3600 секунд")
                db.session.commit()
                flash("Настройки сохранены", "success")
                return redirect(url_for("settings"))
            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), "error")
        return render_template("settings.html", s=s)

    @app.route("/rules", methods=["GET", "POST"])
    @login_required
    def rules():
        if request.method == "POST":
            keyword = request.form.get("keyword", "").strip()
            target = request.form.get("target", "").upper()
            recipient_id = request.form.get("recipient_id", "").strip()
            if not keyword or target not in {"TG", "MAX"} or not recipient_id:
                flash("Заполните все поля правила", "error")
            else:
                db.session.add(Rule(keyword=keyword, target=target, recipient_id=recipient_id))
                db.session.commit()
                flash("Правило добавлено", "success")
                return redirect(url_for("rules"))
        return render_template("rules.html", rules=Rule.query.order_by(Rule.id.desc()).all())

    @app.post("/rules/<int:rule_id>/toggle")
    @login_required
    def toggle_rule(rule_id):
        rule = db.get_or_404(Rule, rule_id)
        rule.enabled = not rule.enabled
        db.session.commit()
        return redirect(url_for("rules"))

    @app.post("/rules/<int:rule_id>/delete")
    @login_required
    def delete_rule(rule_id):
        rule = db.get_or_404(Rule, rule_id)
        db.session.delete(rule)
        db.session.commit()
        flash("Правило удалено", "success")
        return redirect(url_for("rules"))

    @app.route("/history")
    @login_required
    def history():
        logs = DeliveryLog.query.order_by(DeliveryLog.created_at.desc()).limit(200).all()
        return render_template("history.html", logs=logs)

    @app.route("/health")
    def health():
        return {"ok": True, "service": "email-bridge-bot"}

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "10000")), debug=False)
