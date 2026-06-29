# Image with Python 3.12 + Node.js 20 pre-installed
FROM nikolaik/python-nodejs:python3.12-nodejs20

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node deps for WhatsApp bridge
COPY whatsapp_service/package.json whatsapp_service/
RUN cd whatsapp_service && npm install --omit=dev

COPY . .

CMD ["python", "bot.py"]
