# Halyk Covenant Agent — Muse Spark primary + Gemini Vision fallback
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for PyMuPDF, Pillow, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libmupdf-dev gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Ensure cache dir exists and is writable
RUN mkdir -p /app/cache && chmod 755 /app/cache

# Copy agentic data (documents + ledger) is already in image for offline hackathon
# but keep it as-is; submission.json will be written to /app/submission.json

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=3).read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
