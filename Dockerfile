# Stage 1: Base (System dependencies)
FROM python:3.12-alpine AS base

RUN apk update && apk add --no-cache git gcc g++ libffi-dev

WORKDIR /app

# Stage 2: UV Helper (Provides the uv binary for build stages)
FROM base AS uv-helper

# Copy the uv binary directly from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Compile bytecode for faster startup in build stages
ENV UV_COMPILE_BYTECODE=1

# Stage 3: Exporter (Generates requirements.txt)
# Uses uv to export dependencies from uv.lock to requirements.txt
FROM uv-helper AS exporter

COPY pyproject.toml uv.lock ./

# Export production dependencies, excluding dev and hashes
RUN uv export --no-dev --no-hashes --format=requirements-txt --output-file=requirements.txt

# Stage 4: Dependencies (The CACHED Layer)
# Installs libraries using standard pip to keep the layer "clean" (no uv binary)
FROM base AS dependencies

COPY --from=exporter /app/requirements.txt .

ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# We use standard pip here so the final image doesn't need uv
RUN pip install --no-cache-dir -r requirements.txt

# Stage 5: Builder (Builds your application wheel)
FROM uv-helper AS builder

COPY . .
# Builds the wheel and sdist into /app/dist/
RUN uv build

# Stage 6: Final Base (Combines cached deps + app code)
FROM dependencies AS final

COPY --from=builder /app/dist/*.whl .

# Install the application wheel; dependencies are already satisfied by the base layer
RUN pip install --no-cache-dir *.whl

# Stage 7: Targets
FROM final AS bot
CMD ["python", "-OO", "-m", "ionic"]

FROM final AS web
CMD ["python", "-OO", "-m", "ionic.web"]

FROM final AS schemas-recreate
CMD ["python", "-OO", "-m", "ionic.schemas"]
