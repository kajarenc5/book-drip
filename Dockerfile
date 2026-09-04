FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY data/seed ./data/seed

RUN uv sync --frozen --no-dev

ENV ONECHAPTER_HOST=0.0.0.0 \
    ONECHAPTER_PORT=43147 \
    ONECHAPTER_TIMEZONE=Asia/Yerevan

EXPOSE 43147
CMD ["uv", "run", "--no-dev", "onechapter", "serve", "--host", "0.0.0.0", "--port", "43147"]
