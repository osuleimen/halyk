# Деплой Halyk Covenant Agent на halyk.wit.kz

## Что внутри
- `Dockerfile` — `python:3.13-slim` + `muse-spark` (primary) + `Gemini Vision` fallback + `celery[redis]`.
- `docker-compose.yml` — `halyk` (`127.0.0.1:8000`), изолированный `halyk-redis` (`halyk-net`), воркер `halyk-celery`. Том `halyk_cache` для `providers.json`/`submission.json`. **Не конфликтует** с уже существующим Celery/Redis на хосте — отдельный bridge.
- `app/celery_app.py` + `app/tasks.py` — Celery задачи. `api_run` сначала пробует `Celery` (если брокер доступен), иначе `threading` fallback — аккуратно для хоста где Celery уже есть.
- `deploy/nginx-halyk.wit.kz.conf` — **аккуратный** nginx `server` для `halyk.wit.kz` (upstream `127.0.0.1:8000`). Не затирает существующий reverse.
- `deploy/deploy.sh` / `deploy/.env.example` — деплой под root.

## Развертывание (под root на halyk.wit.kz)

```bash
# 1) залить репо на сервер (или git pull если уже есть)
cd /opt/halyk
git pull

# 2) (опционально) если на хосте уже есть Redis для wit.kz — подключи его:
cp deploy/.env.example .env
# nano .env  # раскомментируй CELERY_BROKER_URL=redis://127.0.0.1:6379/1
# иначе оставь по умолчанию — поднимется изолированный halyk-redis

# 3) запустить деплой (бэкап nginx, не трогает другие сайты)
sudo bash deploy/deploy.sh

# 4) проверить
curl -s http://127.0.0.1:8000/api/status | jq .           # должен быть 200
curl -s http://127.0.0.1:8000/api/providers | jq .        # muse_spark enabled
docker compose ps                                          # halyk, halyk-redis, halyk-celery healthy
docker compose logs halyk-celery --tail 50                # воркер слушает
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
