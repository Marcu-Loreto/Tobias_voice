FROM python:3.13-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl curl && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Dependencies (cache layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code
COPY noturna_client.py noturna_agent.py mcp_bridge.py whatsapp_bridge.py ./
COPY prompts/ prompts/

# Runtime dirs
RUN mkdir -p data logs .certs

# EasyPanel exposes HTTP — port 8000
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s \
    CMD curl -f http://localhost:8000/ || exit 1

# Run on HTTP (EasyPanel handles SSL)
CMD ["uv", "run", "uvicorn", "noturna_client:app", "--host", "0.0.0.0", "--port", "8000"]
