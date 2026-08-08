# Halyk Covenant Agent — Muse Spark primary + Gemini Vision fallback — BigTech multi-stage
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for building (PyMuPDF, Pillow need gcc/mupdf)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libmupdf-dev gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r halyk && useradd -r -g halyk -m halyk

COPY --from=builder /wheels /wheels
RUN pip install --no-cache /wheels/* && rm -rf /wheels

COPY --chown=halyk:halyk . .

RUN mkdir -p /app/cache && chown -R halyk:halyk /app/cache /app

USER halyk

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=10 \
  CMD python -c "import urllib.request,sys; data=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).read(); sys.exit(0 if b'ok' in data else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
