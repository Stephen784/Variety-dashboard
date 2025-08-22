# Dockerfile — pin Python 3.11 and create small image for Render
FROM python:3.11-slim

# Avoid buffering and pyc
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install minimal build deps for possible wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Set port
ENV PORT=8080
EXPOSE 8080

# Start command (gunicorn)
CMD ["gunicorn", "app:server", "--bind", "0.0.0.0:8080", "--workers", "1"]
