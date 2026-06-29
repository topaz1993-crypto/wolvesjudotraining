"""
wa_client.py — WhatsApp bridge wrapper (optional feature).
אם Node.js לא מותקן, כל הפונקציות מחזירות ערכי ברירת מחדל בשקט.
"""

import os
import time
import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

WA_PORT    = int(os.environ.get("WA_PORT", 3000))
WA_API_KEY = os.environ.get("WA_API_KEY", "wolves-wa-secret")
BASE_URL   = f"http://127.0.0.1:{WA_PORT}"
HEADERS    = {"x-api-key": WA_API_KEY}

_process = None
_node_available = None  # cached check


def _has_node() -> bool:
    global _node_available
    if _node_available is None:
        try:
            subprocess.run(["node", "--version"], capture_output=True, timeout=5)
            _node_available = True
        except Exception:
            _node_available = False
    return _node_available


def _get_http():
    """Lazy import of httpx to avoid import errors if not installed."""
    try:
        import httpx
        return httpx
    except ImportError:
        return None


def start_service():
    """מפעיל את שירות ה-Node.js כ-subprocess — רק אם Node קיים."""
    global _process
    if not _has_node():
        log.info("Node.js not available — WhatsApp bridge disabled")
        return

    service_dir = Path(__file__).parent / "whatsapp_service"
    if not service_dir.exists():
        log.warning("whatsapp_service/ not found")
        return

    node_modules = service_dir / "node_modules"
    if not node_modules.exists():
        log.info("npm install for WhatsApp bridge...")
        result = subprocess.run(
            ["npm", "install", "--prefix", str(service_dir)],
            capture_output=True, timeout=180
        )
        if result.returncode != 0:
            log.error("npm install failed: %s", result.stderr.decode())
            return

    env = os.environ.copy()
    env["WA_PORT"]    = str(WA_PORT)
    env["WA_API_KEY"] = WA_API_KEY
    env["DATA_DIR"]   = str(Path("/data") if Path("/data").exists() else Path("."))

    _process = subprocess.Popen(
        ["node", str(service_dir / "index.js")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )

    def _log():
        for line in _process.stdout:
            log.info("[WA] %s", line.decode(errors="replace").rstrip())
    threading.Thread(target=_log, daemon=True).start()

    # Wait up to 90s for HTTP
    httpx = _get_http()
    if not httpx:
        return
    for i in range(90):
        try:
            r = httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=2)
            if r.status_code == 200:
                log.info("WA bridge ready after %ds", i)
                return
        except Exception:
            pass
        if _process.poll() is not None:
            log.error("WA bridge died during startup")
            return
        time.sleep(1)


def get_status() -> dict:
    httpx = _get_http()
    if not httpx or not _has_node():
        return {"connected": False, "status": "node_unavailable", "has_qr": False}
    try:
        r = httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=3)
        return r.json()
    except Exception:
        return {"connected": False, "status": "bridge_offline", "has_qr": False}


def get_qr_base64() -> str | None:
    httpx = _get_http()
    if not httpx:
        return None
    try:
        r = httpx.get(f"{BASE_URL}/qr", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "qr" in data:
                qr = data["qr"]
                return qr.split(",", 1)[1] if "," in qr else qr
    except Exception:
        pass
    # File fallback
    qr_file = Path("/data/wa_qr.txt") if Path("/data").exists() else Path("wa_qr.txt")
    if qr_file.exists():
        raw = qr_file.read_text().strip()
        return raw.split(",", 1)[1] if "," in raw else raw
    return None


def force_reconnect() -> bool:
    """מאפס את הסשן ומייצר QR חדש."""
    httpx = _get_http()
    if not httpx:
        return False
    try:
        r = httpx.post(f"{BASE_URL}/reconnect", headers=HEADERS, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        log.error("force_reconnect error: %s", e)
        return False


def get_groups() -> list[dict]:
    """מחזיר רשימת קבוצות WhatsApp: [{id, name, size}]"""
    httpx = _get_http()
    if not httpx:
        return []
    try:
        r = httpx.get(f"{BASE_URL}/groups", headers=HEADERS, timeout=10)
        return r.json().get("groups", [])
    except Exception as e:
        log.error("wa get_groups error: %s", e)
        return []


def send_message(phone: str, message: str) -> bool:
    httpx = _get_http()
    if not httpx:
        return False
    try:
        r = httpx.post(
            f"{BASE_URL}/send", headers=HEADERS,
            json={"to": phone, "message": message}, timeout=15
        )
        return r.json().get("success", False)
    except Exception as e:
        log.error("wa send error: %s", e)
        return False


def is_connected() -> bool:
    return get_status().get("connected", False)


def _process_alive() -> bool:
    """Return True if the WA bridge subprocess is running or the HTTP bridge responds."""
    global _process
    if _process is not None and _process.poll() is None:
        return True
    httpx = _get_http()
    if not httpx:
        return False
    try:
        httpx.get(f"{BASE_URL}/status", headers=HEADERS, timeout=2)
        return True
    except Exception:
        return False
