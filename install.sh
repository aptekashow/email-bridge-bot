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

echo "[1/8] Установка системных пакетов..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl xvfb x11vnc novnc websockify

echo "[2/8] Копирование проекта в $APP_DIR..."
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$APP_DIR"
cp -a "$SOURCE_DIR/." "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 0750 "$APP_DIR"

echo "[3/8] Подготовка виртуального окружения..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[4/8] Установка Chromium для MAX Web..."
"$APP_DIR/.venv/bin/playwright" install-deps chromium
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/playwright" install chromium

echo "[5/8] Создание .env при первой установке..."
if [[ ! -f "$APP_DIR/.env" ]]; then
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "$APP_DIR/.env" <<EOF
SECRET_KEY=$SECRET_KEY
ADMIN_PASSWORD=$ADMIN_PASSWORD
HOST=0.0.0.0
PORT=10000
COOKIE_SECURE=0
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

echo "[6/8] Установка служб systemd..."
sed "s/@SERVICE_USER@/$APP_USER/g" "$APP_DIR/deploy/email-bridge-web.service" \
  > /etc/systemd/system/email-bridge-web.service
sed "s/@SERVICE_USER@/$APP_USER/g" "$APP_DIR/deploy/email-bridge-worker.service" \
  > /etc/systemd/system/email-bridge-worker.service
sed "s/@SERVICE_USER@/$APP_USER/g" "$APP_DIR/deploy/email-bridge-max-web.service" \
  > /etc/systemd/system/email-bridge-max-web.service

install -d -o "$APP_USER" -g "$APP_USER" -m 0700 /opt/email-bridge-data

echo "[7/8] Права и запуск..."
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 0600 "$APP_DIR/.env"
systemctl daemon-reload
systemctl enable --now email-bridge-web.service email-bridge-worker.service email-bridge-max-web.service

echo "[8/8] Проверка..."
sleep 2
systemctl --no-pager --full status email-bridge-web.service email-bridge-worker.service email-bridge-max-web.service || true
echo
echo "Установка завершена."
echo "Панель: http://IP_СЕРВЕРА:10000"
echo "Проверка: curl http://127.0.0.1:10000/health"
echo "Логи Worker: journalctl -u email-bridge-worker -f"
echo
echo "После входа в панель заполни IMAP и Telegram. SOCKS5-прокси загружаются автоматически."
