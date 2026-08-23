FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8067 \
    DATA_DIR=/app/data

# Working directory
WORKDIR /app

# Install system dependencies (curl for healthchecks, tzdata)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app

# Create volume mount point for persistent SQLite db and icons
RUN mkdir -p /app/data /app/data/icons

# Expose Web UI port
EXPOSE 8067

# Run application
CMD ["python", "-m", "app.main"]
