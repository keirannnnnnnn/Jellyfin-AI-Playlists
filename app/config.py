import os
import socket
import logging
from pathlib import Path

logger = logging.getLogger("jellyfin_playlists.config")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "jellyfin_playlists.db"
ICONS_DIR = DATA_DIR / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PORT = int(os.getenv("PORT", "8067"))
DEFAULT_JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://127.0.0.1:8096")
DEFAULT_JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
DEFAULT_JELLYFIN_USERNAME = os.getenv("JELLYFIN_USERNAME", "")
DEFAULT_JELLYFIN_PASSWORD = os.getenv("JELLYFIN_PASSWORD", "")
DEFAULT_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEFAULT_PLAYBACK_DB_PATH = os.getenv("PLAYBACK_DB_PATH", "")
DEFAULT_UI_PASSWORD = os.getenv("DEFAULT_PASSWORD", "Password123")
SESSION_SECRET = os.getenv("SESSION_SECRET", os.urandom(32).hex())


def find_tailscale_ip() -> str | None:
    """Attempt to detect Tailscale IPv4 address (100.64.0.0/10 range)."""
    # 1. Try socket gethostbyname_ex or interface enumeration
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            parts = [int(p) for p in ip.split(".") if p.isdigit()]
            if len(parts) == 4:
                # Tailscale CGNAT range is 100.64.0.0 to 100.127.255.255
                if parts[0] == 100 and (64 <= parts[1] <= 127):
                    return ip
    except Exception as e:
        logger.debug(f"Hostname IP check failed: {e}")

    # 2. Check environment variable
    env_ts_ip = os.getenv("TAILSCALE_IP")
    if env_ts_ip:
        return env_ts_ip

    return None


def get_bind_host() -> str:
    """Determine the host IP to bind the web server to."""
    bind_env = os.getenv("BIND_HOST", "").strip()

    if bind_env in ("tailscale", "tailscale0", "auto"):
        ts_ip = find_tailscale_ip()
        if ts_ip:
            logger.info(f"Resolved Tailscale IP: {ts_ip}")
            return ts_ip
        logger.warning(
            "Tailscale interface/IP not detected. Falling back to 127.0.0.1. "
            "Ensure Tailscale is connected or set BIND_HOST explicitly."
        )
        return "127.0.0.1"

    if bind_env:
        return bind_env

    # Default behaviour: check for Tailscale IP, else 0.0.0.0 or 127.0.0.1
    ts_ip = find_tailscale_ip()
    if ts_ip:
        return ts_ip

    return "0.0.0.0"
