#!/bin/sh
# Railway entrypoint for the Celery worker (same image, different command).
set -e

if [ -n "$DATABASE_URL" ]; then
  export RAG_DATABASE_URL=$(echo "$DATABASE_URL" | sed 's#^postgres://#postgresql://#; s#^postgresql://#postgresql+asyncpg://#')
fi
if [ -n "$REDIS_URL" ]; then
  export RAG_REDIS_URL="$REDIS_URL"
fi

echo "Starting Celery worker..."
exec celery -A rag_platform.worker.celery_app:app worker --loglevel=INFO --concurrency=2
