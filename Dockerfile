FROM python:3.12-slim

# Install Node.js 20 (only curl needed — no Chromium!)
RUN apt-get update \
 && apt-get install -y curl --no-install-recommends \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs --no-install-recommends \
 && apt-get purge -y curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node deps (Baileys — no browser, no Chromium)
COPY whatsapp_service/package.json whatsapp_service/
RUN cd whatsapp_service && npm install --omit=dev

COPY . .

CMD ["python", "bot.py"]
