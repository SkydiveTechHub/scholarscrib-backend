#!/bin/sh

uv sync --frozen && uv cache prune --ci

echo Running migrations

alembic upgrade head 