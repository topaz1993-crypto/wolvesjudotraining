/**
 * Wolves Judo — WhatsApp Bridge Service
 * 
 * Express HTTP server שמנהל את חיבור ה-WhatsApp.
 * הבוט הפייתון מתקשר עם שירות זה דרך localhost.
 * 
 * נקודות קצה:
 *   GET  /status   — מצב החיבור
 *   GET  /qr       — תמונת QR כ-base64
 *   POST /send     — שלח הודעה { to, message }
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const qrcode  = require('qrcode');
const path    = require('path');
const fs      = require('fs');

const PORT    = process.env.WA_PORT    || 3000;
const API_KEY = process.env.WA_API_KEY || 'wolves-wa-secret';
const DATA_DIR = process.env.DATA_DIR  || '/data';

// ── State ──────────────────────────────────────────────
let currentQR     = null;   // latest QR string
let qrBase64      = null;   // QR as PNG base64
let isReady       = false;
let statusMsg     = 'starting';

// ── WhatsApp Client ────────────────────────────────────
const client = new Client({
  authStrategy: new LocalAuth({
    dataPath: path.join(DATA_DIR, 'wa_session')
  }),
  puppeteer: {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--single-process',
      '--disable-gpu'
    ]
  }
});

client.on('qr', async (qr) => {
  currentQR = qr;
  statusMsg = 'waiting_qr';
  isReady   = false;
  try {
    qrBase64 = await qrcode.toDataURL(qr);
    // Also save QR to file so Python can read it
    fs.writeFileSync(path.join(DATA_DIR, 'wa_qr.txt'), qrBase64);
    console.log('[WA] New QR generated');
  } catch (e) {
    console.error('[WA] QR error:', e);
  }
});

client.on('ready', () => {
  isReady   = true;
  statusMsg = 'connected';
  currentQR = null;
  qrBase64  = null;
  // Signal readiness to Python bot
  fs.writeFileSync(path.join(DATA_DIR, 'wa_status.txt'), 'connected');
  console.log('[WA] Client ready!');
});

client.on('authenticated', () => {
  statusMsg = 'authenticated';
  console.log('[WA] Authenticated');
});

client.on('auth_failure', (msg) => {
  statusMsg = 'auth_failed';
  isReady   = false;
  fs.writeFileSync(path.join(DATA_DIR, 'wa_status.txt'), 'disconnected');
  console.error('[WA] Auth failure:', msg);
});

client.on('disconnected', (reason) => {
  isReady   = false;
  statusMsg = 'disconnected';
  fs.writeFileSync(path.join(DATA_DIR, 'wa_status.txt'), 'disconnected');
  console.warn('[WA] Disconnected:', reason);
  // Try reconnect after 10 seconds
  setTimeout(() => {
    console.log('[WA] Attempting reconnect...');
    client.initialize().catch(e => console.error('[WA] Reconnect failed:', e));
  }, 10000);
});

// ── HTTP Server ────────────────────────────────────────
const app = express();
app.use(express.json());

// Simple API key auth
app.use((req, res, next) => {
  if (req.headers['x-api-key'] !== API_KEY) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  next();
});

// GET /status
app.get('/status', (req, res) => {
  res.json({
    connected: isReady,
    status:    statusMsg,
    has_qr:    qrBase64 !== null
  });
});

// GET /qr — returns QR image as base64 PNG
app.get('/qr', (req, res) => {
  if (isReady) {
    return res.json({ connected: true, message: 'Already connected, no QR needed' });
  }
  if (!qrBase64) {
    return res.status(202).json({ message: 'QR not ready yet, try again in a few seconds' });
  }
  res.json({ qr: qrBase64 });
});

// POST /send — send a WhatsApp message
// Body: { to: "972501234567", message: "שלום!" }
app.post('/send', async (req, res) => {
  const { to, message } = req.body;
  if (!to || !message) {
    return res.status(400).json({ error: 'Missing to or message' });
  }
  if (!isReady) {
    return res.status(503).json({ error: 'WhatsApp not connected' });
  }
  try {
    // Normalize number: remove leading 0 and add country code if needed
    let number = to.replace(/\D/g, '');
    if (number.startsWith('0')) {
      number = '972' + number.slice(1);
    }
    const chatId = number + '@c.us';
    await client.sendMessage(chatId, message);
    console.log(`[WA] Sent to ${number}`);
    res.json({ success: true, to: number });
  } catch (e) {
    console.error('[WA] Send error:', e);
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[WA] Bridge listening on port ${PORT}`);
});

// ── Initialize ─────────────────────────────────────────
client.initialize();
