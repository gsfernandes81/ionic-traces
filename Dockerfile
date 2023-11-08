FROM python:3.11-alpine as base

RUN apk update
RUN apk add --no-cache git gcc g++ libffi-dev

WORKDIR /app

FROM base as builder-base

ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.6.1

RUN pip install "poetry==$POETRY_VERSION"
RUN python -m venv /venv

COPY pyproject.toml poetry.lock ./
RUN . /venv/bin/activate && poetry install --without dev --no-root

FROM builder-base as builder

COPY . .
RUN . /venv/bin/activate && poetry build

FROM base as final

COPY --from=builder /venv /venv
COPY --from=builder /app/dist .
COPY docker-entrypoint.sh ./
COPY Procfile ./

RUN . /venv/bin/activate && pip install *.whl
CMD ["sh", "docker-entrypoint.sh"]