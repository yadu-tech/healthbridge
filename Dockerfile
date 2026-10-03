# One image for the pipeline commands and the dashboard. Source is installed in editable mode
# because the code locates sql/ and reference/ relative to the repository root.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so source edits do not invalidate this layer.
COPY pyproject.toml ./
RUN mkdir -p src/healthbridge && touch src/healthbridge/__init__.py \
    && pip install -e ".[dashboard,ml]"

COPY src ./src
COPY sql ./sql
COPY reference ./reference
COPY .streamlit ./.streamlit

RUN useradd --create-home --uid 1000 healthbridge \
    && mkdir -p data/raw data/experiments docs \
    && chown -R healthbridge:healthbridge /app
USER healthbridge

EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"

# Default: the dashboard. Pipeline commands override this (see docker-compose.yml, service "pipeline").
CMD ["streamlit", "run", "src/healthbridge/dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
