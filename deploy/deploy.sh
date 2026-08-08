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

HALYK_PORT="${HALYK_PORT:-18080}"
export HALYK_PORT

echo "[1/5] Сборка образа (порт $HALYK_PORT)..."
cd "$REPO_DIR"
# Чистим старый контейнер если порт занят
if ss -tulpn 2>/dev/null | grep -q ":$HALYK_PORT " ; then
  echo "  Порт $HALYK_PORT занят — ищем кто держит (на cloud-001 заняты 3000,3005,8000,8005,8080,8100 и т.д.):"
  ss -tulpn 2>/dev/null | grep -E ":$HALYK_PORT" || true
  docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E "$HALYK_PORT" || true
  echo "  Пробуем освободить старый halyk контейнер..."
  docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
  # Если всё ещё занят — задай другой: HALYK_PORT=18081 bash deploy/deploy.sh
fi
docker compose -f "$COMPOSE_FILE" build

echo "[2/5] Запуск контейнера (halyk:$HALYK_PORT + halyk-redis + halyk-celery + halyk-beat)..."
docker compose -f "$COMPOSE_FILE" up -d
docker compose -f "$COMPOSE_FILE" ps
sleep 8
curl -sf http://127.0.0.1:$HALYK_PORT/api/status | head -c 500; echo || echo "  (api пока не отвечает — смотри логи ниже)"
echo "  Celery worker:"
docker compose -f "$COMPOSE_FILE" logs halyk-celery --tail 20 2>&1 | tail -n 20 || true
echo "  Celery beat:"
docker compose -f "$COMPOSE_FILE" logs halyk-beat --tail 10 2>&1 | tail -n 10 || true

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
echo "Локально: http://127.0.0.1:$HALYK_PORT/api/status  (внутри контейнера 8000)"
echo "Публично: https://halyk.wit.kz  (если DNS и сертификат настроены, upstream 127.0.0.1:$HALYK_PORT)"
echo "Логи: docker compose -f $COMPOSE_FILE logs -f halyk | halyk-celery | halyk-beat"
echo "Остановка: docker compose -f $COMPOSE_FILE down"
echo "Смена порта: HALYK_PORT=8002 bash deploy/deploy.sh  (и поменяй upstream в nginx-halyk.wit.kz.conf)"
echo "Обновление: git pull && bash deploy/deploy.sh"
