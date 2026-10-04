#!/usr/bin/env bash
# Se corre en el VPS, dentro de /opt/inboxbridge-mcp: trae la última versión y reinicia.
set -euo pipefail
cd "$(dirname "$0")/.."

test -f .env || { echo "Falta .env (copia .env.example y llénalo)"; exit 1; }
git pull --ff-only origin main
docker compose build --pull
docker compose up -d --remove-orphans

echo "Esperando a que el contenedor quede sano..."
for _ in $(seq 1 30); do
  estado=$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q app)")
  [ "$estado" = "healthy" ] && { echo "OK: $(git rev-parse --short HEAD) en línea"; exit 0; }
  sleep 2
done
echo "El contenedor no quedó sano. Últimos logs:"; docker compose logs --tail 40 app; exit 1
