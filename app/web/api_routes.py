import os
import shutil
import logging
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File

from app.config import ICONS_DIR, DB_PATH
from app.auth import require_auth
from app.database import (
    get_db,
    get_all_settings,
    save_mix_definition,
    get_mix_definition,
)
from app.services.jellyfin_client import JellyfinClient
from app.services.gemini_client import GeminiClient
from app.services.generator_service import run_smart_playlists, push_all_mix_icons, fix_all_playlist_access

logger = logging.getLogger("jellyfin_playlists.api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


class RunRequest(BaseModel):
    target_user_id: str | None = None
    target_mix_key: str | None = None
    force: bool = False


class TestJellyfinRequest(BaseModel):
    url: str | None = None
    api_key: str | None = None
    playback_db_path: str | None = None


class TestGeminiRequest(BaseModel):
    api_key: str | None = None
    model: str | None = None


class ToggleUserRequest(BaseModel):
    user_id: str
    enabled: bool


class UpdateMixRequest(BaseModel):
    mix_key: str
    display_name: str
    type: str
    config: dict


@router.post("/run")
async def trigger_run(payload: RunRequest):
    """Trigger an on-demand playlist generation run.

    Returns HTTP 409 Conflict immediately (rather than queuing) if a run is
    already in progress, so the caller gets a clear message and the UI can
    surface it rather than waiting silently.
    """
    result = await run_smart_playlists(
        trigger="manual",
        target_user_id=payload.target_user_id,
        target_mix_key=payload.target_mix_key,
        force=payload.force,
        db_file=DB_PATH,
    )
    if result.get("status") == "already_running":
        raise HTTPException(
            status_code=409,
            detail=result["error"],
        )
    return result


@router.post("/playlists/push-icons")
async def push_icons():
    """Push icons to all existing Jellyfin user playlists matching mix names."""
    result = await push_all_mix_icons(db_file=DB_PATH)
    return result


@router.post("/playlists/fix-access")
async def fix_playlist_access():
    """Retroactively set IsPublic=false on all playlists tracked in user_playlist_state.

    Use this to immediately close server-wide visibility on playlists created before
    the IsPublic access-control fix was deployed.  Safe to call multiple times —
    only touches playlists recorded in this app's DB and skips any already deleted.
    """
    result = await fix_all_playlist_access(db_file=DB_PATH)
    return result


@router.post("/test/jellyfin")
async def test_jellyfin_endpoint(payload: TestJellyfinRequest):
    """Test Jellyfin API connection and report diagnostics."""
    settings = get_all_settings(DB_PATH)
    url = payload.url or settings.get("jellyfin_url", "")
    api_key = payload.api_key or settings.get("jellyfin_api_key", "")
    playback_db_path = payload.playback_db_path or settings.get("playback_db_path", "")

    client = JellyfinClient(
        base_url=url,
        api_key=api_key,
        playback_db_path=playback_db_path,
    )
    res = await client.test_connection()
    return res


@router.post("/test/gemini")
async def test_gemini_endpoint(payload: TestGeminiRequest):
    """Test Gemini API connection, update status table, and return diagnostics."""
    settings = get_all_settings(DB_PATH)
    api_key = payload.api_key if payload.api_key is not None else settings.get("gemini_api_key", "")
    model = payload.model or settings.get("gemini_model", "gemini-1.5-flash")

    client = GeminiClient(api_key=api_key, model=model)
    res = await client.test_connection()
    return res


@router.post("/users/toggle")
async def toggle_user(payload: ToggleUserRequest):
    """Enable or disable playlist generation for a specific Jellyfin user."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET enabled = ? WHERE jellyfin_user_id = ?;",
            (1 if payload.enabled else 0, payload.user_id),
        )
    return {"success": True, "user_id": payload.user_id, "enabled": payload.enabled}


@router.post("/mixes/update")
async def update_mix(payload: UpdateMixRequest):
    """Update configuration and display name for a mix definition."""
    save_mix_definition(
        mix_key=payload.mix_key,
        display_name=payload.display_name,
        mix_type=payload.type,
        config=payload.config,
        db_file=DB_PATH,
    )
    return {"success": True, "mix_key": payload.mix_key}


@router.post("/mixes/{mix_key}/icon")
async def upload_mix_icon(mix_key: str, icon_file: UploadFile = File(...)):
    """Upload a custom icon image for a mix type."""
    mix = get_mix_definition(mix_key, db_file=DB_PATH)
    if not mix:
        raise HTTPException(status_code=404, detail="Mix not found")

    ext = Path(icon_file.filename or "icon.png").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
        raise HTTPException(status_code=400, detail="Unsupported image format. Use PNG, JPEG, SVG or WebP.")

    dest_filename = f"{mix_key}{ext}"
    dest_path = ICONS_DIR / dest_filename

    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(icon_file.file, buffer)

    # Save relative icon path in DB
    relative_icon_path = f"data/icons/{dest_filename}"
    save_mix_definition(
        mix_key=mix["mix_key"],
        display_name=mix["display_name"],
        mix_type=mix["type"],
        config=mix["config"],
        icon_path=relative_icon_path,
        enabled=mix["enabled"],
        db_file=DB_PATH,
    )

    return {"success": True, "icon_path": relative_icon_path}
