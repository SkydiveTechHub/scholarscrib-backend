#!/bin/sh

echo "Running migrations"
alembic upgrade heads
WEB_PORT=${PORT:-8001}

echo "Starting server"
fastapi run --port $PORT