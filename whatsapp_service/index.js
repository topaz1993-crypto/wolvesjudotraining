/**
 * Wolves Judo — WhatsApp Bridge (Baileys — no Chromium)
 *
 * GET  /status   — connection status
 * GET  /qr       — QR as base64 PNG
 * POST /send     — { to, message }
 */

import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion
} from "@whiskeysockets/baileys";
import express    from "express";
import qrcode     from "qrcode";
import { Boom }   from "@hapi/boom";
import pino       from "pino";
import { existsSync, mkdirSync, writeFileSync } from "fs";
import { join }   from "path";

const PORT     = process.env.WA_PORT    || 3000;
const API_KEY  = process.env.WA_API_KEY || "wolves-wa-secret";
const DATA_DIR = process.env.DATA_DIR   || "/data";
const AUTH_DIR = join(DATA_DIR, "wa_baileys_auth");

if (!existsSync(AUTH_DIR)) mkdirSync(AUTH_DIR, { recursive: true });

// ── State ──────────────────────────────────────────────
let sock         = null;
let qrBase64     = null;
let isConnected  = false;
let statusMsg    = "starting";

const logger = pino({ level: "silent" }); // suppress Baileys verbose logs

// ── Connect ────────────────────────────────────────────
async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version }          = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth:           state,
    logger,
    printQRInTerminal: false,
    browser: ["Wolves Judo", "Chrome", "1.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      statusMsg = "waiting_qr";
      isConnected = false;
      try {
        qrBase64 = await qrcode.toDataURL(qr);
        writeFileSync(join(DATA_DIR, "wa_qr.txt"), qrBase64);
        console.log("[WA] QR generated");
      } catch (e) {
        console.error("[WA] QR error:", e);
      }
    }

    if (connection === "open") {
      isConnected = true;
      statusMsg   = "connected";
      qrBase64    = null;
      writeFileSync(join(DATA_DIR, "wa_status.txt"), "connected");
      console.log("[WA] Connected!");
    }

    if (connection === "close") {
      isConnected = false;
      const code  = lastDisconnect?.error instanceof Boom
        ? lastDisconnect.error.output?.statusCode
        : 0;
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      statusMsg = shouldReconnect ? "reconnecting" : "logged_out";
      writeFileSync(join(DATA_DIR, "wa_status.txt"), statusMsg);
      console.log(`[WA] Disconnected (${code}) — reconnect: ${shouldReconnect}`);
      if (shouldReconnect) {
        setTimeout(connectToWhatsApp, 5000);
      }
    }
  });
}

// ── HTTP Server ────────────────────────────────────────
const app = express();
app.use(express.json());

app.use((req, res, next) => {
  if (req.headers["x-api-key"] !== API_KEY) {
    return res.status(401).json({ error: "unauthorized" });
  }
  next();
});

app.get("/status", (req, res) => {
  res.json({ connected: isConnected, status: statusMsg, has_qr: qrBase64 !== null });
});

app.get("/qr", (req, res) => {
  if (isConnected)  return res.json({ connected: true });
  if (!qrBase64)    return res.status(202).json({ message: "QR not ready yet" });
  res.json({ qr: qrBase64 });
});

app.post("/reconnect", async (req, res) => {
  console.log("[WA] Force reconnect requested — clearing session");
  isConnected = false;
  statusMsg   = "reconnecting";
  qrBase64    = null;
  // Clear auth files so a fresh QR is generated
  try {
    const { rmSync } = await import("fs");
    rmSync(AUTH_DIR, { recursive: true, force: true });
    mkdirSync(AUTH_DIR, { recursive: true });
  } catch (e) {
    console.warn("[WA] Could not clear auth dir:", e.message);
  }
  if (sock) {
    try { sock.end(); } catch(_) {}
    sock = null;
  }
  res.json({ ok: true });
  setTimeout(() => connectToWhatsApp().catch(console.error), 1000);
});

app.get("/groups", async (req, res) => {
  if (!isConnected) return res.status(503).json({ error: "Not connected" });
  try {
    const chats = await sock.groupFetchAllParticipating();
    const groups = Object.values(chats).map(g => ({
      id:   g.id,
      name: g.subject,
      size: g.participants?.length || 0
    }));
    groups.sort((a, b) => a.name.localeCompare(b.name, "he"));
    res.json({ groups });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/send", async (req, res) => {
  const { to, message } = req.body;
  if (!to || !message)  return res.status(400).json({ error: "Missing to/message" });
  if (!isConnected)     return res.status(503).json({ error: "Not connected" });

  try {
    let jid;
    if (to.endsWith("@g.us")) {
      // Group JID — use as-is
      jid = to;
    } else {
      let number = to.replace(/\D/g, "");
      if (number.startsWith("0")) number = "972" + number.slice(1);
      jid = number + "@s.whatsapp.net";
    }
    await sock.sendMessage(jid, { text: message });
    console.log(`[WA] Sent to ${jid}`);
    res.json({ success: true, to: jid });
  } catch (e) {
    console.error("[WA] Send error:", e);
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`[WA] Bridge on port ${PORT}`);
});

connectToWhatsApp().catch(e => console.error("[WA] Init error:", e));
