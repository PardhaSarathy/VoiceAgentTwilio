FROM python:3.11-slim

# Install ffmpeg (required by pipecat for audio processing)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Render assigns a dynamic port via $PORT (defaults to 10000)
ENV PORT=10000
EXPOSE ${PORT}

# Bind to 0.0.0.0 on Render's dynamic $PORT
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT}
