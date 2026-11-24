#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/email-bridge-bot"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Запусти установщик через sudo: sudo bash install.sh"
  exit 1
fi

if [[ -z "$APP_USER" || "$APP_USER" == "root" ]] || ! id "$APP_USER" >/dev/null 2>&1; then
  echo "Не удалось определить обычного пользователя. Запусти: sudo bash install.sh"
  echo "Установщик должен быть запущен из-под пользовательской SSH-сессии через sudo."
  exit 1
fi

echo "[1/7] Установка системных пакетов..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl

echo "[2/7] Копирование проекта в $APP_DIR..."
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$APP_DIR"
cp -a "$SOURCE_DIR/." "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 0750 "$APP_DIR"

echo "[3/7] Подготовка виртуального окружения..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[4/7] Создание .env при первой установке..."
if [[ ! -f "$APP_DIR/.env" ]]; then
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "$APP_DIR/.env" <<EOF
SECRET_KEY=$SECRET_KEY
ADMIN_PASSWORD=$ADMIN_PASSWORD
HOST=0.0.0.0
PORT=10000
COOKIE_SECURE=0
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
EOF
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 0600 "$APP_DIR/.env"
  echo
  echo "СОХРАНИ ПАРОЛЬ АДМИНИСТРАТОРА (он показан только при первой установке):"
  echo "$ADMIN_PASSWORD"
  echo
else
  echo "Существующий .env сохранён без изменений."
fi

echo "[5/7] Установка служб systemd..."
sed "s/@SERVICE_USER@/$APP_USER/g" "$APP_DIR/deploy/email-bridge-web.service" \
  > /etc/systemd/system/email-bridge-web.service
sed "s/@SERVICE_USER@/$APP_USER/g" "$APP_DIR/deploy/email-bridge-worker.service" \
  > /etc/systemd/system/email-bridge-worker.service
sed "s/@SERVICE_USER@/$APP_USER/g" "$APP_DIR/deploy/email-bridge-max.service" \
  > /etc/systemd/system/email-bridge-max.service

echo "[6/7] Права и запуск..."
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 0600 "$APP_DIR/.env"
systemctl daemon-reload
systemctl enable --now email-bridge-web.service email-bridge-worker.service email-bridge-max.service

echo "[7/7] Проверка..."
sleep 2
systemctl --no-pager --full status email-bridge-web.service email-bridge-worker.service email-bridge-max.service || true
echo
echo "Установка завершена."
echo "Панель: http://IP_СЕРВЕРА:10000"
echo "Проверка: curl http://127.0.0.1:10000/health"
echo "Логи почты: journalctl -u email-bridge-worker -f"
echo "Логи MAX: journalctl -u email-bridge-max -f"
echo
echo "После входа в панель заполни IMAP и Telegram. SOCKS5-прокси загружаются автоматически."
