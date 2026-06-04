FROM python:3.11-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy pipeline source
COPY pipeline.py scenes.py gallery.py server.py start-ui.sh ./

RUN chmod +x start-ui.sh

# Output dir (override via volume mount)
RUN mkdir -p /app/output

EXPOSE 8765

CMD ["python3", "server.py", "--port", "8765"]
