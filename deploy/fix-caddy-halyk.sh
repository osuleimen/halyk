#!/usr/bin/env bash
set -euo pipefail
# Аккуратно чинит путь Caddyfile для halyk.wit.kz на cloud-001
# Ошибка: open /opt/dev/Caddyfile: no such file — внутри контейнера файл всегда /etc/caddy/Caddyfile

echo "== Halyk Caddy path fix =="

# 1) Найти где реально лежит Caddyfile хоста, который смонтирован в onaiu_caddy
echo "[1] Ищем Caddyfile хоста..."
for p in /opt/dev/Caddyfile /opt/onaiu/Caddyfile ./Caddyfile /opt/halyk/Caddyfile; do
  if [[ -f "$p" ]]; then echo "  FOUND: $p"; ls -l "$p" | head -n 2; fi
done
echo "  Mounts onaiu_caddy:"
docker inspect onaiu_caddy --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{println}}{{end}}' 2>&1 | grep -i caddy || echo "  (не удалось inspect — проверь docker ps)"

echo ""
echo "[2] Проверяем что snippet добавлен в ПРАВИЛЬНЫЙ файл (тот что смонтирован)..."
SRC=""
for cand in /opt/dev/Caddyfile /opt/onaiu/Caddyfile; do
  if docker inspect onaiu_caddy 2>/dev/null | grep -q "$cand"; then SRC="$cand"; break; fi
done
if [[ -z "$SRC" ]]; then
  # fallback: ищем по Mounts
  SRC=$(docker inspect onaiu_caddy --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
fi
echo "  Source Caddyfile (хост) = ${SRC:-не найден}"

if [[ -n "$SRC" && -f "$SRC" ]]; then
  echo "  Последние 25 строк $SRC:"
  tail -n 25 "$SRC" | sed 's/^/    /'
  if grep -q "halyk.wit.kz" "$SRC"; then
    echo "  ✅ halyk.wit.kz уже в $SRC"
  else
    echo "  ❌ halyk.wit.kz НЕТ в $SRC — добавляем из deploy/Caddyfile.halyk.snippet"
    # Ищем где лежит snippet
    SNIPPET=""
    for s in /opt/halyk/deploy/Caddyfile.halyk.snippet ./deploy/Caddyfile.halyk.snippet deploy/Caddyfile.halyk.snippet; do
      [[ -f "$s" ]] && SNIPPET="$s" && break
    done
    if [[ -n "$SNIPPET" ]]; then
      cat "$SNIPPET" >> "$SRC"
      echo "  Добавлено из $SNIPPET"
    else
      echo "  snippet не найден — скопируй вручную"
    fi
  fi
fi

echo ""
echo "[3] Валидация ВНУТРИ контейнера (правильный путь /etc/caddy/Caddyfile)..."
docker exec onaiu_caddy caddy validate --config /etc/caddy/Caddyfile && echo "  ✅ validate OK" || echo "  ❌ validate FAILED — смотри лог выше"

echo ""
echo "[4] Если validate OK — reload:"
echo "  docker exec onaiu_caddy caddy reload --config /etc/caddy/Caddyfile"
echo "  docker logs onaiu_caddy --tail 30 | grep -i halyk"
echo "  curl -s https://halyk.wit.kz/api/status | jq ."

echo ""
echo "Подсказка: НЕ запускай 'caddy validate --config /opt/dev/Caddyfile' внутри контейнера — внутри всегда /etc/caddy/Caddyfile"
