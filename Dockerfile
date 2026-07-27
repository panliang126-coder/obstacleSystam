FROM python:3.11-slim AS test

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

COPY pyproject.toml README.md requirements-dev.lock ./
COPY src ./src
COPY schemas ./schemas
COPY proto ./proto
COPY tests ./tests

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements-dev.lock \
    && python -m pip install --no-cache-dir --no-deps -e .

RUN ruff check src tests \
    && mypy src \
    && obstacle-schema validate-examples \
    && pytest --cov=low_altitude_ai --cov-report=term-missing
