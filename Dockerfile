FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 payments && useradd --uid 10001 --gid payments --no-create-home payments \
    && mkdir /data && chown payments:payments /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
USER 10001:10001
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "cryptopayments"]
CMD ["--help"]
