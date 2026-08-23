FROM python:3.12-slim

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

WORKDIR /app
COPY apps/platform-api/requirements.txt .
RUN pip install -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app
COPY --chown=app:app apps/platform-api/app ./app
COPY --chown=app:app migrations ./migrations
RUN mkdir -p /app/data && chown app:app /app/data

EXPOSE 8000
USER app
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
