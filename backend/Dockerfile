FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py
RUN uv pip install --system --no-cache-dir ".[dev]"

EXPOSE 8000
