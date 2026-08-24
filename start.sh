#!/bin/sh
# Railway entrypoint for the API service.
#
# Railway injects DATABASE_URL (postgresql://...) and REDIS_URL from the
# Postgres/Redis plugins, and assigns a dynamic $PORT. This app reads RAG_*
# settings and uses the async driver, so we translate the URLs once here.
set -e

: "${PORT:=8000}"

# postgresql://  ->  postgresql+asyncpg://   (app + alembic both use async)
if [ -n "$DATABASE_URL" ]; then
  export RAG_DATABASE_URL=$(echo "$DATABASE_URL" | sed 's#^postgres://#postgresql://#; s#^postgresql://#postgresql+asyncpg://#')
fi
if [ -n "$REDIS_URL" ]; then
  export RAG_REDIS_URL="$REDIS_URL"
fi

echo "Running database migrations (also CREATE EXTENSION vector)..."
alembic upgrade head

echo "Starting API on port $PORT..."
exec uvicorn rag_platform.main:app --host 0.0.0.0 --port "$PORT"
