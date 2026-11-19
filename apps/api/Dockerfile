FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY alembic.ini ./
COPY migrations migrations
COPY packages packages
COPY apps/api apps/api
COPY apps/mcp apps/mcp
COPY data/demo data/demo
COPY scripts/seed_demo.py scripts/seed_demo.py
