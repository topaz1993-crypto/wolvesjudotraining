"""
wa_client.py — Python wrapper for the WhatsApp bridge service.

הבוט הפייתון קורא לפונקציות כאן כדי לשלוח הודעות WhatsApp.
השירות עצמו (Node.js) רץ כ-subprocess.
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
DATA_DIR   = Path("/data") if Path("/data").exists() else Path(".")

_process: subprocess.Popen | None = None
_started = False


def start_service():
    """מפעיל את שירות ה-Node.js כ-subprocess."""
    global _process, _started
    if _started:
        return

    service_dir = Path(__file__).parent / "whatsapp_service"
    if not service_dir.exists():
        log.warning("whatsapp_service/ not found — skipping WA bridge")
        return

    # Install npm packages if needed
    node_modules = service_dir / "node_modules"
    if not node_modules.exists():
        log.info("Installing WhatsApp service npm packages...")
        subprocess.run(["npm", "install", "--prefix", str(service_dir)],
                       capture_output=True, timeout=120)

    env = os.environ.copy()
    env["WA_PORT"]    = str(WA_PORT)
    env["WA_API_KEY"] = WA_API_KEY
    env["DATA_DIR"]   = str(DATA_DIR)

    _process = subprocess.Popen(
        ["node", str(service_dir / "index.js")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    _started = True

    # Log output in background thread
    def _log_output():
        for line in _process.stdout:
            log.info("[WA-node] %s", line.decode(errors="replace").rstrip())
    threading.Thread(target=_log_output, daemon=True).start()

    # Wait for service to be ready
    for _ in range(30):
        try:
            r = httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=2)
            if r.status_code == 200:
                log.info("WhatsApp bridge ready")
                return
        except Exception:
            pass
        time.sleep(1)
    log.warning("WhatsApp bridge did not respond in 30s")


def get_status() -> dict:
    """מחזיר מצב החיבור."""
    try:
        r = httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=5)
        return r.json()
    except Exception as e:
        return {"connected": False, "status": f"error: {e}", "has_qr": False}


def get_qr_base64() -> str | None:
    """מחזיר QR כ-base64 PNG, או None אם לא זמין."""
    try:
        r = httpx.get(f"{BASE_URL}/qr", headers=HEADERS, timeout=10)
        data = r.json()
        if "qr" in data:
            # data:image/png;base64,XXXX → extract the base64 part
            qr = data["qr"]
            if "," in qr:
                qr = qr.split(",", 1)[1]
            return qr
    except Exception as e:
        log.error("get_qr_base64 error: %s", e)
    return None


def send_message(phone: str, message: str) -> bool:
    """
    שולח הודעת WhatsApp.
    phone: מספר ישראלי — 050XXXXXXX או 9725XXXXXXX
    מחזיר True אם נשלח בהצלחה.
    """
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
    return get_status().get("connected", False)
