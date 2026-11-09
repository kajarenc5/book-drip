FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE manage.py ./
COPY src ./src
COPY data/seed ./data/seed
COPY infra/scripts ./infra/scripts

RUN uv sync --frozen --no-dev \
    && DJANGO_DEBUG=0 uv run --no-dev python manage.py collectstatic --noinput

ENV ONECHAPTER_HOST=0.0.0.0 \
    ONECHAPTER_PORT=43147 \
    ONECHAPTER_TIMEZONE=Asia/Yerevan

EXPOSE 43147
CMD ["uv", "run", "--no-dev", "onechapter", "serve", "--host", "0.0.0.0", "--port", "43147"]
