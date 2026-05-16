FROM python:3.11-slim

WORKDIR /app

# Build tools required by some Python packages (faiss-cpu, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies as a separate layer so it's cached on re-builds
COPY Requirements.txt .
RUN pip install --no-cache-dir -r Requirements.txt

# Copy application code
COPY . .

# Persistent storage for SQLite DB and FAISS indexes
RUN mkdir -p /app/storage

EXPOSE 8001

CMD ["uvicorn", "RAG_Frontend:app", "--host", "0.0.0.0", "--port", "8001"]
