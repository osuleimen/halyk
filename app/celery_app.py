"""
Celery app для Halyk Covenant Agent — аккуратно использует уже существующий брокер на halyk.wit.kz если он есть.

Приоритет:
  1) CELERY_BROKER_URL из env (например, redis://halyk-redis:6379/1 или amqp://...)
  2) если на хосте уже крутится Redis/RabbitMQ для wit.kz — задай его URL в .env и compose подхватит
  3) иначе fallback на threading внутри api_run (никаких падений)

Воркер и beat опциональны — docker-compose запускает их в том же стеке, но можно
использовать внешний Celery на хосте, тогда просто выстави CELERY_BROKER_URL.

Для Flower (мониторинг) — отдельный сервис, не мешает основному reverse.
"""
import os

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://halyk-redis:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://halyk-redis:6379/2")

try:
    from celery import Celery

    celery_app = Celery(
        "halyk",
        broker=CELERY_BROKER_URL,
        backend=CELERY_RESULT_BACKEND,
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Almaty",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=3600,
        task_soft_time_limit=3300,
        worker_prefetch_multiplier=1,
        beat_schedule={
            # Автоматическая проверка сертификата halyk.wit.kz — не мешает хостовому celery beat
            "halyk-cert-renew-check": {
                "task": "app.tasks.cert_renew_check",
                "schedule": 86400.0,  # раз в сутки
                "options": {"expires": 3600},
            },
        },
    )

    # Авто-поиск задач в app.*
    celery_app.autodiscover_tasks(["app"])

except ImportError:
    # Celery не установлен — воркеры не нужны, API будет работать через threading
    celery_app = None


def is_celery_available() -> bool:
    """Проверяет, доступен ли брокер — не валим прод, если Redis там не поднят."""
    if celery_app is None:
        return False
    # Broker URL должен быть задан и не пустой
    broker = os.getenv("CELERY_BROKER_URL", CELERY_BROKER_URL)
    if not broker:
        return False
    # Быстрая проверка доступности Redis если это redis://
    if broker.startswith("redis://"):
        try:
            import socket
            from urllib.parse import urlparse
            p = urlparse(broker)
            host = p.hostname or "127.0.0.1"
            port = p.port or 6379
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            return True
        except Exception:
            return False
    # Для amqp и др. — считаем доступным, Celery сам ретрайнит
    return True
