# Stage 1: Base (System dependencies)
FROM python:3.12-alpine AS base

RUN apk update
RUN apk add --no-cache git gcc g++ libffi-dev

WORKDIR /app


# Stage 2: Poetry Setup (Used for exporting and building)
FROM base AS poetry-helper

ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.6.1

RUN pip install "poetry==$POETRY_VERSION"

COPY pyproject.toml poetry.lock ./


# Stage 3: Exporter (Generates requirements.txt)
# We export to requirements.txt so we can install without Poetry in the next stage.
# This keeps the final image cleaner.
FROM poetry-helper AS exporter

RUN poetry export --without dev --without-hashes --format=requirements.txt > requirements.txt


# Stage 4: Dependencies (The CACHED Layer)
# This stage installs libraries into the global python environment.
# It DOES NOT contain your source code or Poetry.
# It only invalidates if pyproject.toml or poetry.lock changes.
FROM base AS dependencies

COPY --from=exporter /app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Stage 5: Builder (Builds your application wheel)
# This stage needs the source code, so it comes after the cached dependency layer.
FROM poetry-helper AS builder

COPY . .
RUN poetry build


# Stage 6: Final Base (Combines cached deps + app code)
FROM dependencies AS final

COPY --from=builder /app/dist/*.whl .

# Install only the application wheel (dependencies are already in the base layer)
RUN pip install --no-cache-dir *.whl


# Stage 7: Targets
FROM final AS bot
CMD ["python", "-OO", "-m", "ionic"]

FROM final AS web
CMD ["python", "-OO", "-m", "ionic.web"]

FROM final AS schemas-recreate
CMD ["python", "-OO", "-m", "ionic.schemas"]
