import json
import logging
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, DB_PATH, find_tailscale_ip
from app.auth import (
    COOKIE_NAME,
    create_session_token,
    is_authenticated,
    require_auth_page,
)
from app.database import (
    get_db,
    get_all_settings,
    get_setting,
    set_setting,
    get_mix_definitions,
    get_gemini_status,
    verify_password,
    hash_password,
)
from app.scheduler import get_scheduler_status, update_scheduler_job
from app.services.mix_engine import filter_tracks_by_mix

logger = logging.getLogger("jellyfin_playlists.routes")

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))
router = APIRouter()


def get_template_context(request: Request, active_page: str = "", extra: dict | None = None) -> dict:
    ctx = {
        "request": request,
        "is_authenticated": is_authenticated(request),
        "active_page": active_page,
        "gemini_status": get_gemini_status(),
        "tailscale_ip": find_tailscale_ip(),
    }
    if extra:
        ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
async def index_redirect(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/dashboard", error: str | None = None):
    if is_authenticated(request):
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next_url": next, "error": error, "is_authenticated": False},
    )


@router.post("/login")
async def login_action(request: Request, password: str = Form(...), next: str = Form("/dashboard")):
    stored_hash = get_setting("password_hash")
    if verify_password(password, stored_hash):
        resp = RedirectResponse(url=next or "/dashboard", status_code=303)
        token = create_session_token()
        resp.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=86400 * 30,
            httponly=True,
            samesite="lax",
        )
        return resp
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next_url": next, "error": "Invalid password", "is_authenticated": False},
    )


@router.get("/logout")
async def logout_action():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    with get_db() as conn:
        cursor = conn.cursor()

        # Fetch users
        cursor.execute("SELECT * FROM users ORDER BY username;")
        users = [dict(row) for row in cursor.fetchall()]

        # Fetch user playlist states
        cursor.execute("SELECT * FROM user_playlist_state;")
        states = [dict(row) for row in cursor.fetchall()]
        user_playlist_map: dict[str, list] = {}
        for st in states:
            user_playlist_map.setdefault(st["user_id"], []).append(st)

        # Fetch last run
        cursor.execute("SELECT * FROM run_log ORDER BY started_at DESC LIMIT 1;")
        last_run_row = cursor.fetchone()
        last_run = dict(last_run_row) if last_run_row else None

        # Fetch recent 5 runs
        cursor.execute("SELECT * FROM run_log ORDER BY started_at DESC LIMIT 5;")
        recent_runs_raw = [dict(row) for row in cursor.fetchall()]
        recent_runs = []
        for r in recent_runs_raw:
            dur_str = "-"
            if r.get("started_at") and r.get("finished_at"):
                try:
                    s_dt = datetime.fromisoformat(r["started_at"])
                    f_dt = datetime.fromisoformat(r["finished_at"])
                    dur_str = f"{(f_dt - s_dt).total_seconds():.1f}s"
                except Exception:
                    pass
            r["duration_str"] = dur_str
            recent_runs.append(r)

    mixes = get_mix_definitions()
    scheduler_status = get_scheduler_status()

    active_user_count = sum(1 for u in users if u.get("enabled"))
    total_user_count = len(users)

    ctx = get_template_context(
        request,
        active_page="dashboard",
        extra={
            "users": users,
            "user_playlist_map": user_playlist_map,
            "last_run": last_run,
            "recent_runs": recent_runs,
            "mix_count": len(mixes),
            "active_user_count": active_user_count,
            "total_user_count": total_user_count,
            "scheduler_status": scheduler_status,
        },
    )
    return templates.TemplateResponse(request=request, name="dashboard.html", context=ctx)


@router.get("/playlists", response_class=HTMLResponse)
async def playlists_page(request: Request):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    mixes = get_mix_definitions()
    ctx = get_template_context(
        request,
        active_page="playlists",
        extra={"mixes": mixes},
    )
    return templates.TemplateResponse(request=request, name="playlists.html", context=ctx)


@router.get("/trigger", response_class=HTMLResponse)
async def trigger_page(request: Request):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT jellyfin_user_id, username FROM users WHERE enabled = 1 ORDER BY username;")
        users = [dict(row) for row in cursor.fetchall()]

    mixes = get_mix_definitions()
    ctx = get_template_context(
        request,
        active_page="trigger",
        extra={"users": users, "mixes": mixes},
    )
    return templates.TemplateResponse(request=request, name="trigger.html", context=ctx)


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM run_log ORDER BY started_at DESC LIMIT 30;")
        runs_raw = [dict(row) for row in cursor.fetchall()]

        runs = []
        for r in runs_raw:
            run_id = r["run_id"]
            cursor.execute(
                "SELECT * FROM run_log_entries WHERE run_id = ? ORDER BY entry_id ASC;",
                (run_id,),
            )
            r["entries"] = [dict(row) for row in cursor.fetchall()]
            summary_dict = {}
            if r.get("summary"):
                try:
                    summary_dict = json.loads(r["summary"])
                except Exception:
                    pass
            r["summary_dict"] = summary_dict

            dur_str = "-"
            if r.get("started_at") and r.get("finished_at"):
                try:
                    s_dt = datetime.fromisoformat(r["started_at"])
                    f_dt = datetime.fromisoformat(r["finished_at"])
                    dur_str = f"{(f_dt - s_dt).total_seconds():.1f}s"
                except Exception:
                    pass
            r["duration_str"] = dur_str
            runs.append(r)

    ctx = get_template_context(
        request,
        active_page="logs",
        extra={"runs": runs},
    )
    return templates.TemplateResponse(request=request, name="logs.html", context=ctx)


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM songs;")
        songs = []
        for row in cursor.fetchall():
            s = dict(row)
            try:
                s["genres"] = json.loads(s.get("genres_json") or "[]")
            except Exception:
                s["genres"] = []
            songs.append(s)

        cursor.execute("SELECT COUNT(*) FROM users WHERE enabled = 1;")
        active_users_count = cursor.fetchone()[0]

    total_songs = len(songs)
    bpm_songs = [s for s in songs if s.get("bpm") is not None]
    bpm_count = len(bpm_songs)
    bpm_pct = f"{(bpm_count / total_songs * 100):.1f}" if total_songs > 0 else "0.0"

    # Mix pool calculations
    mixes = get_mix_definitions()
    pool_stats = []
    for mix in mixes:
        m_type = mix["type"]
        m_cfg = mix["config"]
        target_count = m_cfg.get("target_track_count", 40)
        min_count = m_cfg.get("min_track_count", 10)

        if m_type in ("genre", "decade"):
            cand = filter_tracks_by_mix(songs, m_type, m_cfg)
            cand_count = len(cand)
        elif m_type == "driving":
            # Tier 1 + 2 quick check
            min_bpm = m_cfg.get("min_bpm", 115)
            max_bpm = m_cfg.get("max_bpm", 145)
            bpm_cands = [s for s in songs if s.get("bpm") is not None and min_bpm <= s["bpm"] <= max_bpm]
            if len(bpm_cands) >= min_count:
                cand_count = len(bpm_cands)
            else:
                allow_g = m_cfg.get("energy_allow_genres", [])
                from app.services.mix_engine import genre_matches
                genre_cands = [s for s in songs if genre_matches(s.get("genres", []), allow_g)]
                cand_count = len(genre_cands)
        else:
            cand_count = 0

        pool_stats.append({
            "display_name": mix["display_name"],
            "mix_key": mix["mix_key"],
            "type": m_type,
            "candidate_count": cand_count,
            "target_count": target_count,
            "min_count": min_count,
        })

    # Genre breakdown
    genre_freq: dict[str, int] = {}
    for s in songs:
        for g in s.get("genres", []):
            g_clean = g.strip().title()
            if g_clean:
                genre_freq[g_clean] = genre_freq.get(g_clean, 0) + 1
    top_genres = sorted(genre_freq.items(), key=lambda x: x[1], reverse=True)[:25]

    ctx = get_template_context(
        request,
        active_page="stats",
        extra={
            "total_songs": total_songs,
            "bpm_songs_count": bpm_count,
            "bpm_percentage": bpm_pct,
            "active_users_count": active_users_count,
            "pool_stats": pool_stats,
            "top_genres": top_genres,
        },
    )
    return templates.TemplateResponse(request=request, name="stats.html", context=ctx)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, success: str | None = None, error: str | None = None):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    settings = get_all_settings()
    ctx = get_template_context(
        request,
        active_page="settings",
        extra={
            "settings": settings,
            "success_message": success,
            "error_message": error,
        },
    )
    return templates.TemplateResponse(request=request, name="settings.html", context=ctx)


@router.post("/settings/jellyfin")
async def update_jellyfin_settings(
    request: Request,
    jellyfin_url: str = Form(...),
    jellyfin_api_key: str = Form(...),
    playback_db_path: str = Form(""),
):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    set_setting("jellyfin_url", jellyfin_url.strip())
    set_setting("jellyfin_api_key", jellyfin_api_key.strip())
    set_setting("playback_db_path", playback_db_path.strip())
    return RedirectResponse(url="/settings?success=Jellyfin+settings+saved+successfully", status_code=303)


@router.post("/settings/gemini")
async def update_gemini_settings(
    request: Request,
    gemini_api_key: str = Form(""),
    gemini_model: str = Form("gemini-1.5-flash"),
):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    set_setting("gemini_api_key", gemini_api_key.strip())
    set_setting("gemini_model", gemini_model.strip())
    return RedirectResponse(url="/settings?success=Gemini+AI+settings+saved+successfully", status_code=303)


@router.post("/settings/schedule")
async def update_schedule_settings(
    request: Request,
    schedule_hour: str = Form("2"),
    schedule_minute: str = Form("0"),
    schedule_enabled: str = Form("false"),
):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    set_setting("schedule_hour", schedule_hour.strip())
    set_setting("schedule_minute", schedule_minute.strip())
    set_setting("schedule_enabled", "true" if schedule_enabled == "true" else "false")
    update_scheduler_job()
    return RedirectResponse(url="/settings?success=Daily+schedule+updated+successfully", status_code=303)


@router.post("/settings/password")
async def update_password_settings(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    auth_check = require_auth_page(request)
    if auth_check:
        return auth_check

    if new_password != confirm_password:
        return RedirectResponse(url="/settings?error=New+passwords+do+not+match", status_code=303)
    if len(new_password) < 4:
        return RedirectResponse(url="/settings?error=Password+must+be+at+least+4+characters", status_code=303)

    new_hash = hash_password(new_password)
    set_setting("password_hash", new_hash)
    return RedirectResponse(url="/settings?success=UI+password+changed+successfully", status_code=303)
