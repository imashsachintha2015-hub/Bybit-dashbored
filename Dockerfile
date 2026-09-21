FROM python:3.11-slim

WORKDIR /app

# Install essential system tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project
COPY . .

# Ensure entrypoint is executable
RUN chmod +x start_all.sh

# Expose Railway web port
EXPOSE 8080

# Run unified launcher
CMD ["./start_all.sh"]
