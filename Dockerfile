# Production service. Python 3.13 matches the supported Chroma runtime.
FROM python:3.13.12-slim@sha256:f1927c75e81efd1e091dbd64b6c0ecaa5630b38635a3d1c04034ac636e1f94c8
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production SEED_ON_EMPTY=0 WEB_CONCURRENCY=1 \
    PERSISTENT_STORAGE_ROOT=/var/data \
    DATABASE_PATH=/var/data/sql/service.sqlite3 \
    DOCUMENTS_DIR=/var/data/documents VECTORS_DIR=/var/data/vectors \
    EMAILS_DIR=/var/data/legacy-emails CHROMA_DIR=/var/data/legacy-chroma
RUN useradd --create-home --uid 1000 user && mkdir -p /var/data /app && chown user:user /var/data /app
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY --chown=user:user api.py ./
COPY --chown=user:user src/ ./src/
COPY --chown=user:user web/ ./web/
COPY --chown=user:user deploy/render-supabase/pricing-zero.json ./deploy/render-supabase/pricing-zero.json
USER 1000:1000
EXPOSE 7860
CMD ["python", "-m", "src.operations.launch"]
