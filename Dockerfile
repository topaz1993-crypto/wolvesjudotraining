FROM python:3.12-slim

# Install Node.js 20 + Chromium for whatsapp-web.js
RUN apt-get update && apt-get install -y \
    curl gnupg ca-certificates \
    chromium \
    fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libdbus-1-3 libgdk-pixbuf2.0-0 libnspr4 \
    libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 \
    xdg-utils libgbm1 libxshmfence1 \
    --no-install-recommends \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*

# Tell puppeteer to use system Chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node deps for WhatsApp bridge
COPY whatsapp_service/package.json whatsapp_service/
RUN cd whatsapp_service && npm install --omit=dev

COPY . .

CMD ["python", "bot.py"]
