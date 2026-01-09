# Stage 1: Base (System dependencies)
FROM python:3.12-alpine AS base

RUN apk update && apk add --no-cache git gcc g++ libffi-dev tzdata
ENV TZ=Etc/UTC

WORKDIR /app

# Stage 2: UV Helper (Provides the uv binary for build stages)
FROM base AS uv-helper

# Copy the uv binary directly from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Compile bytecode for faster startup in build stages
ENV UV_COMPILE_BYTECODE=1

# Stage 3: Exporter (Generates requirements.txt)
FROM uv-helper AS exporter

COPY pyproject.toml uv.lock ./

# FIXED: Added --no-emit-project so the requirements.txt ONLY contains
# third-party dependencies, not the local app itself.
RUN uv export --no-dev --no-hashes --no-emit-project --format=requirements-txt --output-file=requirements.txt

# Stage 4: Dependencies (The CACHED Layer)
FROM base AS dependencies

COPY --from=exporter /app/requirements.txt .

ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Now this only installs third-party libs, so it won't fail looking for source code
RUN pip install --no-cache-dir -r requirements.txt

# Stage 5: Builder (Builds your application wheel)
FROM uv-helper AS builder

COPY . .
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
