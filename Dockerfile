FROM python:3.12-slim

ARG APP_GIT_SHA=unknown
ARG APP_BUILD_TIME_UTC=unknown
ARG APP_VERSION=unknown
ARG APP_BUILD_ID=unknown
ARG APP_IMAGE_DIGEST=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=dev \
    DEPLOYMENT_READ_ONLY=true \
    ALLOW_PRODUCTION_MUTATIONS=false \
    IDENTITY_AUTHORIZATION_ENABLED=true \
    IDENTITY_ADMIN_WRITES_ENABLED=false \
    VOLUNTEER_SERVICE_INVITATIONS_ENABLED=false \
    MEMBER_SERVICE_SIGNAL_FEEDBACK_ENABLED=false \
    RUN_BOOTSTRAP_ON_STARTUP=false \
    MCP_ALLOWED_HOSTS=seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com

ENV APP_GIT_SHA=${APP_GIT_SHA} \
    APP_BUILD_TIME_UTC=${APP_BUILD_TIME_UTC} \
    APP_VERSION=${APP_VERSION} \
    APP_BUILD_ID=${APP_BUILD_ID} \
    APP_IMAGE_DIGEST=${APP_IMAGE_DIGEST}

LABEL org.opencontainers.image.revision="${APP_GIT_SHA}" \
      org.opencontainers.image.created="${APP_BUILD_TIME_UTC}" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/spring-chao/seiwajyuku-platform" \
      org.opencontainers.image.digest="${APP_IMAGE_DIGEST}" \
      com.seiwajyuku.build.id="${APP_BUILD_ID}"

WORKDIR /app
COPY apps/platform-api/requirements.txt .
RUN pip install -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app
COPY --chown=app:app apps/platform-api/app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app data/learning-plans ./data/learning-plans
RUN mkdir -p /app/data && chown app:app /app/data

EXPOSE 8000
USER app
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
