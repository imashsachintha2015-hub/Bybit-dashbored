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

# Make the large client controller observable without replacing the live
# source file in git. The patcher is assertion-based: if its anchors no longer
# match the current app.js, the image build fails instead of silently shipping
# an incomplete supervisor/risk trace.
RUN python tools/inject_live_pipeline_trace.py

# Ensure entrypoint is executable
RUN chmod +x start_all.sh

# Expose Railway web port
EXPOSE 8080

# Run unified launcher
CMD ["python", "launcher.py"]
