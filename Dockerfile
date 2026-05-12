FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml first for caching
COPY pyproject.toml ./

# Install uv for fast package management
RUN pip install uv

# Install dependencies
RUN uv sync --frozen --no-install-project

# Copy source code
COPY src/ ./src/
COPY migrations/ ./migrations/

# Copy migrations to expected location (migrations runner looks for src/../migrations)
RUN mkdir -p src/migrations && cp -r migrations/* src/migrations/

# Set Python path
ENV PYTHONPATH=/app/src

# Run the application
CMD ["python", "-m", "cortex.main"]
