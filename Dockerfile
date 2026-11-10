FROM python:3.12-slim

WORKDIR /app

# Install system dependencies + PostgreSQL 16 client tools (PGDG).
# python:3.12-slim is Debian 13 (trixie); Debian's own package is newer than 16,
# and the pre-migration dump needs a pg_dump compatible with the compose server.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject + lockfile first for caching
COPY pyproject.toml uv.lock ./

# Install uv for fast package management
RUN pip install uv

# Install dependencies into the project venv
RUN uv sync --frozen --no-install-project

# Copy source code
COPY src/ ./src/
COPY migrations/ ./migrations/

# Copy migrations to expected location (migrations runner looks for src/../migrations)
RUN mkdir -p src/migrations && cp -r migrations/* src/migrations/

# Set Python path and use the uv-managed venv
ENV PYTHONPATH=/app/src
ENV PATH="/app/.venv/bin:$PATH"

# Run the application (__main__ serves uvicorn)
CMD ["python", "-m", "cortex"]
