FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files (data/ is NOT in git — it lives in Supabase)
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY dashboard.html ./dashboard.html

# Set PYTHONPATH so backend modules can import each other
ENV PYTHONPATH=/app/backend

# Default command (overridden by docker-compose)
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
