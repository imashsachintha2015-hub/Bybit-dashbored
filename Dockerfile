FROM node:20-alpine

WORKDIR /app

# Install dependencies first for better caching
COPY package*.json ./
RUN npm install --omit=dev

# Copy application code
COPY . .

# Start headless MASIS live runner
CMD ["node", "--max-old-space-size=192", "server/live-engine.js"]
