# Деплой Halyk Covenant Agent на halyk.wit.kz

## Что внутри
- `Dockerfile` — `python:3.13-slim` + `muse-spark` (primary) + `Gemini Vision` fallback + `celery[redis]`.
- `docker-compose.yml` — `halyk` (`127.0.0.1:8000`), изолированный `halyk-redis` (`halyk-net`), воркер `halyk-celery`. Том `halyk_cache` для `providers.json`/`submission.json`. **Не конфликтует** с уже существующим Celery/Redis на хосте — отдельный bridge.
- `app/celery_app.py` + `app/tasks.py` — Celery задачи. `api_run` сначала пробует `Celery` (если брокер доступен), иначе `threading` fallback — аккуратно для хоста где Celery уже есть.
- `deploy/nginx-halyk.wit.kz.conf` — **аккуратный** nginx `server` для `halyk.wit.kz` (upstream `127.0.0.1:8000`). Не затирает существующий reverse.
- `deploy/deploy.sh` / `deploy/.env.example` — деплой под root.

## Развертывание на cloud-001 (там уже Caddy, не Nginx)

### Вариант A — через существующий onaiu_caddy (рекомендуется, аккуратно)
Caddy уже слушает 80/443 (контейнер `onaiu_caddy` Up 2 months, healthy) — не ставь Nginx рядом, просто подключи Halyk к его сети:

```bash
cd /opt/halyk && git pull

# 1) убедиться что сеть есть (создана onaiu стеком)
docker network ls | grep onaiu_network || docker network create onaiu_network

# 2) подключить halyk к onaiu_network (уже в docker-compose.yml) и поднять
docker compose up -d
docker compose ps  # halyk, halyk-redis, halyk-celery, halyk-beat

# 3) аккуратно добавить halyk.wit.kz в Caddyfile onaiu (не затирать весь файл)
# на хосте Caddyfile обычно в /opt/onaiu/Caddyfile или ./Caddyfile
cat deploy/Caddyfile.halyk.snippet  # посмотри блок halyk.wit.kz
# вставь его в конец существующего Caddyfile:
cat >> /opt/onaiu/Caddyfile < deploy/Caddyfile.halyk.snippet
# или если onaiu Caddyfile в другом месте:
# cat >> ./Caddyfile < deploy/Caddyfile.halyk.snippet

# 4) перезагрузить Caddy без даунтайма (проверка внутри контейнера)
docker exec onaiu_caddy caddy validate --config /etc/caddy/Caddyfile
docker exec onaiu_caddy caddy reload --config /etc/caddy/Caddyfile
# логи
docker logs onaiu_caddy --tail 50 | grep -i halyk

# 5) проверить
curl -s http://127.0.0.1:18080/api/status | jq .  # напрямую halyk
curl -s https://halyk.wit.kz/api/status | jq .    # через Caddy (TLS автоматом от Caddy)
```

### Вариант B — если оставишь Nginx (запасной)
```bash
# halyk уже на 18080 (свободен, 8000/8080/8100 заняты по ss)
sudo bash deploy/deploy.sh  # ставит nginx-halyk.wit.kz.conf на 18080
sudo nginx -t && sudo systemctl reload nginx
```

## Reverse proxy — аккуратно

Скрипт **не перезаписывает** весь `/etc/nginx/nginx.conf`:
- копирует `deploy/nginx-halyk.wit.kz.conf` → `/etc/nginx/sites-available/halyk.wit.kz`
- делает симлинк в `sites-enabled` **только если его ещё нет**
- делает бэкап предыдущего файла `*.bak.YYYY...`
- `nginx -t` перед `reload`

Если `halyk.wit.kz` уже проксируется другим конфигом — просто добавь в него `location` блок вручную из файла-примера, или подключи этот файл через `include`.

TLS: раскомментируй `ssl_certificate` в примере, или оставь как есть если `certbot --nginx` уже настроен.

## API ключи

В UI: `https://halyk.wit.kz` → вкладка **Провайдеры** → вставь ключ Muse Spark и/или Gemini → `Сохранить` → `Тест`.
Ключи хранятся в `halyk_cache` томе (`/var/lib/docker/volumes/...`), переживают пересборку.

## Обновление

```bash
cd /opt/halyk && git pull && sudo bash deploy/deploy.sh
```

## Откат

```bash
sudo cp /etc/nginx/sites-available/halyk.wit.kz.bak.* /etc/nginx/sites-available/halyk.wit.kz
sudo nginx -t && sudo systemctl reload nginx
docker compose down && docker compose up -d
```
