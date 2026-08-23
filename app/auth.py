import hmac
import hashlib
import time
import base64
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from app.config import SESSION_SECRET
from app.database import get_setting, verify_password, hash_password

COOKIE_NAME = "jellyfin_playlist_session"
SESSION_MAX_AGE = 86400 * 30  # 30 days


def create_session_token() -> str:
    """Generate a tamper-proof signed session token."""
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = f"{timestamp}:{signature}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def verify_session_token(token: str | None) -> bool:
    """Validate token signature and expiration."""
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        timestamp_str, signature = raw.split(":", 1)
        timestamp = int(timestamp_str)

        # Check expiration
        if time.time() - timestamp > SESSION_MAX_AGE:
            return False

        # Verify HMAC
        expected_sig = hmac.new(
            SESSION_SECRET.encode("utf-8"),
            timestamp_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    return verify_session_token(token)


def require_auth(request: Request):
    """Dependency for API routes returning 401 if unauthenticated."""
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return True


def require_auth_page(request: Request):
    """Helper for HTML page routes redirecting to /login if unauthenticated."""
    if not is_authenticated(request):
        next_url = str(request.url.path)
        return RedirectResponse(url=f"/login?next={next_url}", status_code=status.HTTP_303_SEE_OTHER)
    return None
