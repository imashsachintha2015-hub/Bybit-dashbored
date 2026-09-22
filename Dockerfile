FROM python:3.11-slim

WORKDIR /app

# Install essential system tools, Node.js and Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install python and node dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY package*.json ./
RUN npm install --omit=dev --no-audit || true

# Copy entire project
COPY . .

# Ensure entrypoint is executable
RUN chmod +x start_all.sh

# Expose Railway web port
EXPOSE 8080

# Run unified launcher
CMD ["./start_all.sh"]
