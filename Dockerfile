FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
