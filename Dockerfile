FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY dashboard.html ./dashboard.html

# Default command (overridden by docker-compose)
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
