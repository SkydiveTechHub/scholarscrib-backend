FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY main.py alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
