FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TZ=UTC
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin beo \
    && mkdir -p /app/data \
    && chown beo:beo /app/data
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY --from=build /build/app /app/app
USER root
EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.ops.entrypoint"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
