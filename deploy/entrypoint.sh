#!/bin/sh
# Fix volume permissions — halyk_cache is created as root on first mount
mkdir -p /app/cache
chown -R halyk:halyk /app/cache 2>/dev/null || chmod -R 777 /app/cache 2>/dev/null || true
# также чиним agentic-bank-public если туда грузили ZIP от root
chmod -R 755 /app/agentic-bank-public 2>/dev/null || true
exec "$@"
