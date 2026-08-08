#!/usr/bin/env bash
set -euo pipefail

# Halyk Covenant Agent — деплой на halyk.wit.kz (под root)
# Запускай на сервере:  bash deploy/deploy.sh
# Скрипт аккуратен: не трогает другие сайты в /etc/nginx

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NGINX_AVAILABLE="/etc/nginx/sites-available/halyk.wit.kz"
NGINX_ENABLED="/etc/nginx/sites-enabled/halyk.wit.kz"
COMPOSE_FILE="$REPO_DIR/docker-compose.yml"

echo "== Halyk deploy =="
echo "Repo: $REPO_DIR"

if [[ $EUID -ne 0 ]]; then
  echo "Запусти под root: sudo bash deploy/deploy.sh"
  exit 1
fi

command -v docker >/dev/null 2>&1 || { echo "docker не найден. Установи docker + compose plugin."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose plugin не найден."; exit 1; }

echo "[1/5] Сборка образа..."
cd "$REPO_DIR"
docker compose -f "$COMPOSE_FILE" build

echo "[2/5] Запуск контейнера (halyk + halyk-redis + halyk-celery)..."
# Если на хосте уже есть Celery/Redis для wit.kz — можно задать внешний брокер:
# echo "CELERY_BROKER_URL=redis://host.docker.internal:6379/1" > .env
# Скрипт аккуратно использует внутренний halyk-redis если .env не задан (изолированная сеть halyk-net, не мешает хосту)
docker compose -f "$COMPOSE_FILE" up -d
docker compose -f "$COMPOSE_FILE" ps
sleep 8
curl -sf http://127.0.0.1:8000/api/status | head -c 500; echo
echo "  Celery worker:"
docker compose -f "$COMPOSE_FILE" logs halyk-celery --tail 20 2>&1 | tail -n 20 || true

echo "[3/5] Установка nginx-конфига (аккуратно)..."
mkdir -p "$(dirname "$NGINX_AVAILABLE")"
if [[ -f "$NGINX_AVAILABLE" ]]; then
  cp "$NGINX_AVAILABLE" "${NGINX_AVAILABLE}.bak.$(date +%Y%m%d-%H%M%S)"
  echo "  бэкап: ${NGINX_AVAILABLE}.bak.*"
fi
cp "$REPO_DIR/deploy/nginx-halyk.wit.kz.conf" "$NGINX_AVAILABLE"

# Включаем site только если его ещё нет (не затираем руками созданный симлинк)
if [[ ! -e "$NGINX_ENABLED" ]]; then
  ln -s "$NGINX_AVAILABLE" "$NGINX_ENABLED"
  echo "  симлинк создан: $NGINX_ENABLED"
else
  echo "  симлинк уже существует — оставляем как есть"
fi

# Проверяем, не дублируется ли server_name в других конфигах
echo "  Проверка дубликатов server_name halyk.wit.kz..."
grep -R "server_name.*halyk.wit.kz" /etc/nginx/sites-enabled/ || true

echo "[4/5] Проверка nginx..."
nginx -t

echo "[5/5] Перезагрузка nginx..."
systemctl reload nginx || service nginx reload

echo "— Готово —"
echo "Локально: http://127.0.0.1:8000/api/status"
echo "Публично: https://halyk.wit.kz  (если DNS и сертификат настроены)"
echo "Логи контейнера: docker compose -f $COMPOSE_FILE logs -f"
echo "Остановка: docker compose -f $COMPOSE_FILE down"
echo "Обновление: git pull && bash deploy/deploy.sh"
