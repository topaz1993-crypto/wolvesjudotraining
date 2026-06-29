"""
wa_client.py — Python wrapper for the WhatsApp bridge service.
"""

import os
import time
import base64
import logging
import subprocess
import threading
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

WA_PORT    = int(os.environ.get("WA_PORT", 3000))
WA_API_KEY = os.environ.get("WA_API_KEY", "wolves-wa-secret")
BASE_URL   = f"http://127.0.0.1:{WA_PORT}"
HEADERS    = {"x-api-key": WA_API_KEY}

_process: subprocess.Popen | None = None


def _data_dir() -> Path:
    return Path("/data") if Path("/data").exists() else Path(".")


def _process_alive() -> bool:
    return _process is not None and _process.poll() is None


def start_service():
    """מפעיל את שירות ה-Node.js כ-subprocess."""
    global _process

    if _process_alive():
        return  # Already running

    service_dir = Path(__file__).parent / "whatsapp_service"
    if not service_dir.exists():
        log.warning("whatsapp_service/ not found — WA bridge disabled")
        return

    env = os.environ.copy()
    env["WA_PORT"]                  = str(WA_PORT)
    env["WA_API_KEY"]               = WA_API_KEY
    env["DATA_DIR"]                 = str(_data_dir())
    env["PUPPETEER_EXECUTABLE_PATH"] = "/usr/bin/chromium"
    env["PUPPETEER_SKIP_CHROMIUM_DOWNLOAD"] = "true"

    log.info("Starting WhatsApp bridge...")
    _process = subprocess.Popen(
        ["node", str(service_dir / "index.js")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )

    def _log_output():
        for line in _process.stdout:
            log.info("[WA] %s", line.decode(errors="replace").rstrip())
    threading.Thread(target=_log_output, daemon=True).start()

    # Wait up to 90s for HTTP server to respond
    for i in range(90):
        try:
            r = httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=2)
            if r.status_code == 200:
                log.info("WhatsApp bridge HTTP ready (after %ds)", i)
                return
        except Exception:
            pass
        if not _process_alive():
            log.error("WA bridge process died during startup")
            return
        time.sleep(1)
    log.warning("WhatsApp bridge did not respond in 90s")


def ensure_running():
    """Restart service if it died."""
    if not _process_alive():
        log.info("WA bridge not running — restarting")
        threading.Thread(target=start_service, daemon=True).start()


def get_status() -> dict:
    ensure_running()
    try:
        r = httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=5)
        return r.json()
    except Exception as e:
        return {"connected": False, "status": f"bridge_error: {e}", "has_qr": False}


def get_qr_base64() -> str | None:
    """מחזיר QR כ-base64 PNG מה-HTTP endpoint, עם fallback לקובץ."""
    # Try HTTP first
    try:
        r = httpx.get(f"{BASE_URL}/qr", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "qr" in data:
                qr = data["qr"]
                return qr.split(",", 1)[1] if "," in qr else qr
    except Exception:
        pass

    # Fallback: read from file written by Node.js
    qr_file = _data_dir() / "wa_qr.txt"
    if qr_file.exists():
        raw = qr_file.read_text().strip()
        return raw.split(",", 1)[1] if "," in raw else raw

    return None


def send_message(phone: str, message: str) -> bool:
    try:
        r = httpx.post(
            f"{BASE_URL}/send",
            headers=HEADERS,
            json={"to": phone, "message": message},
            timeout=15
        )
        return r.json().get("success", False)
    except Exception as e:
        log.error("wa send_message error: %s", e)
        return False


def is_connected() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=3)
        return r.json().get("connected", False)
    except Exception:
        return False
