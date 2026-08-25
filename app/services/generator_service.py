import asyncio
import uuid
import json
import logging
from datetime import datetime
from pathlib import Path

from app.config import DB_PATH, BASE_DIR
from app.database import (
    get_db,
    get_all_settings,
    get_mix_definitions,
    get_mix_definition,
    set_gemini_status,
)
from app.services.jellyfin_client import JellyfinClient
from app.services.gemini_client import GeminiClient
from app.services.mix_engine import (
    filter_tracks_by_mix,
    compute_track_weights,
    select_weighted_tracks,
)
from app.services.driving_engine import get_driving_mix_candidates

logger = logging.getLogger("jellyfin_playlists.generator")

# Module-level lock preventing concurrent runs (scheduled + manual, or two manual triggers).
# asyncio.Lock is safe for use across a single event loop in FastAPI/uvicorn.
_run_lock = asyncio.Lock()



def load_icon_bytes(icon_path: str | None) -> tuple[bytes | None, str]:
    """Resolve icon file bytes and MIME type from relative or absolute path."""
    if not icon_path:
        return None, "image/png"

    p = Path(icon_path)
    if not p.is_absolute():
        p = BASE_DIR / icon_path

    if not p.exists() or not p.is_file():
        # Try checking static/icons
        alt = BASE_DIR / "app" / icon_path
        if alt.exists() and alt.is_file():
            p = alt
        else:
            return None, "image/png"

    ext = p.suffix.lower()
    content_type = "image/png"
    if ext in (".jpg", ".jpeg"):
        content_type = "image/jpeg"
    elif ext == ".svg":
        content_type = "image/svg+xml"
    elif ext == ".webp":
        content_type = "image/webp"

    try:
        return p.read_bytes(), content_type
    except Exception as e:
        logger.warning(f"Could not read icon file {p}: {e}")
        return None, "image/png"


async def run_smart_playlists(
    trigger: str = "manual",
    target_user_id: str | None = None,
    target_mix_key: str | None = None,
    force: bool = False,
    db_file: Path | str = DB_PATH,
) -> dict:
    """Core playlist generation engine.

    Called by both the APScheduler daily cron and the Web UI manual trigger.
    Returns immediately with status='already_running' if another run is in progress
    rather than queuing behind it or running concurrently.
    """
    # Overlapping-run guard: try to acquire the non-blocking lock.
    # If it's already held (scheduled or another manual run), return a clear error
    # rather than letting two runs write to the same playlists concurrently.
    if not _run_lock.locked():
        acquired = _run_lock
    else:
        logger.warning("run_smart_playlists: rejected — a run is already in progress.")
        return {
            "run_id": None,
            "status": "already_running",
            "error": (
                "A generation run is already in progress. "
                "Wait for it to finish before triggering another."
            ),
        }

    async with acquired:
        return await _run_smart_playlists_inner(
            trigger=trigger,
            target_user_id=target_user_id,
            target_mix_key=target_mix_key,
            force=force,
            db_file=db_file,
        )


async def _run_smart_playlists_inner(
    trigger: str = "manual",
    target_user_id: str | None = None,
    target_mix_key: str | None = None,
    force: bool = False,
    db_file: Path | str = DB_PATH,
) -> dict:
    """Actual generation logic — called only when the run lock is held."""
    run_id = str(uuid.uuid4())
    start_time = datetime.now()

    settings = get_all_settings(db_file)
    jellyfin_url = settings.get("jellyfin_url", "")
    jellyfin_api_key = settings.get("jellyfin_api_key", "")
    jellyfin_username = settings.get("jellyfin_username", "")
    jellyfin_password = settings.get("jellyfin_password", "")
    gemini_api_key = settings.get("gemini_api_key", "")
    gemini_model = settings.get("gemini_model", "gemini-1.5-flash")
    playback_db_path = settings.get("playback_db_path", "")

    # Initialize clients
    jf_client = JellyfinClient(
        base_url=jellyfin_url,
        api_key=jellyfin_api_key,
        username=jellyfin_username,
        password=jellyfin_password,
        playback_db_path=playback_db_path,
    )
    gem_client = GeminiClient(api_key=gemini_api_key, model=gemini_model)

    # Record run in database
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO run_log (run_id, started_at, trigger, status, summary)
            VALUES (?, ?, ?, 'running', ?);
            """,
            (run_id, start_time.isoformat(), trigger, "Run started"),
        )

    summary_stats = {
        "generated": 0,
        "skipped_no_activity": 0,
        "skipped_thin_pool": 0,
        "errors": 0,
        "users_processed": 0,
    }

    try:
        # 1. Fetch library catalog and sync to songs table
        logger.info("Fetching audio items from Jellyfin...")
        all_tracks = await jf_client.get_all_audio_items()
        sync_time = datetime.now().isoformat()

        with get_db(db_file) as conn:
            cursor = conn.cursor()
            for t in all_tracks:
                cursor.execute(
                    """
                    INSERT INTO songs (item_id, title, artist, album, genres_json, production_year, duration_ticks, bpm, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        title = excluded.title,
                        artist = excluded.artist,
                        album = excluded.album,
                        genres_json = excluded.genres_json,
                        production_year = excluded.production_year,
                        duration_ticks = excluded.duration_ticks,
                        bpm = excluded.bpm,
                        last_synced_at = excluded.last_synced_at;
                    """,
                    (
                        t["item_id"],
                        t["title"],
                        t["artist"],
                        t["album"],
                        json.dumps(t["genres"]),
                        t["production_year"],
                        t["duration_ticks"],
                        t["bpm"],
                        sync_time,
                    ),
                )

        # 2. Fetch users from Jellyfin and sync
        jf_users = await jf_client.get_users()
        with get_db(db_file) as conn:
            cursor = conn.cursor()
            for u in jf_users:
                cursor.execute(
                    """
                    INSERT INTO users (jellyfin_user_id, username, enabled)
                    VALUES (?, ?, ?)
                    ON CONFLICT(jellyfin_user_id) DO UPDATE SET
                        username = excluded.username;
                    """,
                    (u["id"], u["name"], 0 if u.get("is_disabled") else 1),
                )

            # Retrieve active users from DB
            if target_user_id:
                cursor.execute(
                    "SELECT jellyfin_user_id, username, last_processed_at FROM users WHERE jellyfin_user_id = ?;",
                    (target_user_id,),
                )
            else:
                cursor.execute(
                    "SELECT jellyfin_user_id, username, last_processed_at FROM users WHERE enabled = 1;"
                )
            users_to_process = [dict(row) for row in cursor.fetchall()]

        # 3. Retrieve Mix Definitions
        all_mixes = get_mix_definitions(db_file)
        if target_mix_key:
            mixes_to_run = [m for m in all_mixes if m["mix_key"] == target_mix_key and m["enabled"]]
        else:
            mixes_to_run = [m for m in all_mixes if m["enabled"]]

        # Pre-compute driving candidates if driving mix is active
        driving_candidates_cache: dict[str, list[dict]] = {}

        # 4. Process each user
        for user in users_to_process:
            u_id = user["jellyfin_user_id"]
            u_name = user["username"]
            last_processed = user["last_processed_at"]
            summary_stats["users_processed"] += 1

            # Check for new activity since last_processed_at
            is_first_run = last_processed is None

            new_events = []
            if not is_first_run:
                new_events = await jf_client.get_playback_activity(u_id, since_iso=last_processed)

            # Apply skip logic: if not first run, not forced, and zero new events -> skip user
            if not is_first_run and not force and len(new_events) == 0:
                logger.info(f"User '{u_name}' skipped: no new playback activity since {last_processed}.")
                with get_db(db_file) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO run_log_entries (run_id, user_id, username, mix_key, status, detail, track_count, created_at)
                        VALUES (?, ?, ?, 'all', 'skipped_no_activity', 'No new listening activity since last generation', 0, ?);
                        """,
                        (run_id, u_id, u_name, datetime.now().isoformat()),
                    )
                summary_stats["skipped_no_activity"] += len(mixes_to_run)
                continue

            # User has activity (or is first run / forced) -> fetch full history for weighting
            full_history = await jf_client.get_playback_activity(u_id)
            activity_map = {}
            for ev in full_history:
                iid = ev["item_id"]
                d_str = ev.get("date_created")
                if iid not in activity_map:
                    activity_map[iid] = {"play_count": 0, "last_played": d_str}
                activity_map[iid]["play_count"] += 1
                if d_str and (not activity_map[iid]["last_played"] or d_str > activity_map[iid]["last_played"]):
                    activity_map[iid]["last_played"] = d_str

            # Load the playlist IDs this app previously recorded for this user.
            # We NEVER look up Jellyfin's live playlist list by name.  The only
            # playlist IDs we will ever pass to update_playlist_items or
            # set_playlist_image are ones stored here from a prior run of this app.
            with get_db(db_file) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT mix_key, jellyfin_playlist_id
                    FROM user_playlist_state
                    WHERE user_id = ? AND jellyfin_playlist_id IS NOT NULL;
                    """,
                    (u_id,),
                )
                db_tracked_playlists: dict[str, str] = {
                    row["mix_key"]: row["jellyfin_playlist_id"]
                    for row in cursor.fetchall()
                }

            # Generate each mix for the user
            for mix in mixes_to_run:
                m_key = mix["mix_key"]
                m_name = mix["display_name"]
                m_type = mix["type"]
                m_cfg = mix["config"]
                target_count = m_cfg.get("target_track_count", 40)
                min_count = m_cfg.get("min_track_count", 10)

                try:
                    # Filter candidates
                    if m_type in ("genre", "decade"):
                        candidates = filter_tracks_by_mix(all_tracks, m_type, m_cfg)
                        resolution_method = "filter"
                    elif m_type == "driving":
                        if "driving" not in driving_candidates_cache:
                            driving_candidates, res_method = await get_driving_mix_candidates(
                                all_tracks, m_cfg, gem_client
                            )
                            driving_candidates_cache["driving"] = driving_candidates
                            resolution_method = res_method
                        else:
                            driving_candidates = driving_candidates_cache["driving"]
                            resolution_method = "cached"
                        candidates = driving_candidates
                    else:
                        candidates = []
                        resolution_method = "unknown"

                    # Compute weights and select tracks
                    weighted = compute_track_weights(candidates, activity_map)
                    selected_ids, sel_status = select_weighted_tracks(
                        weighted, target_count=target_count, min_count=min_count
                    )

                    if sel_status == "skipped_thin_pool":
                        logger.warning(f"User '{u_name}' mix '{m_name}' skipped: thin pool ({len(candidates)} candidates).")
                        with get_db(db_file) as conn:
                            cursor = conn.cursor()
                            cursor.execute(
                                """
                                INSERT INTO run_log_entries (run_id, user_id, username, mix_key, status, detail, track_count, created_at)
                                VALUES (?, ?, ?, ?, 'skipped_thin_pool', ?, 0, ?);
                                """,
                                (
                                    run_id,
                                    u_id,
                                    u_name,
                                    m_key,
                                    f"Thin candidate pool: only {len(candidates)} tracks found (min {min_count} required)",
                                    datetime.now().isoformat(),
                                ),
                            )
                            cursor.execute(
                                """
                                INSERT INTO user_playlist_state (user_id, mix_key, last_status)
                                VALUES (?, ?, 'skipped_thin_pool')
                                ON CONFLICT(user_id, mix_key) DO UPDATE SET last_status = excluded.last_status;
                                """,
                                (u_id, m_key),
                            )
                        summary_stats["skipped_thin_pool"] += 1
                        continue

                    # ---------------------------------------------------------------
                    # Playlist targeting — DB-state-only safety rule
                    # ---------------------------------------------------------------
                    tracked_id = db_tracked_playlists.get(m_key)
                    playlist_id: str
                    access_needs_fixing = False  # True if we need to retroactively close access

                    if tracked_id:
                        still_exists = await jf_client.playlist_exists(tracked_id, u_id)
                        if still_exists:
                            await jf_client.update_playlist_items(tracked_id, u_id, selected_ids)
                            playlist_id = tracked_id
                            action_note = f"Updated in-place with {len(selected_ids)} tracks"
                            # Ensure access is still locked — the playlist may have
                            # been created before the IsPublic fix was deployed.
                            access_needs_fixing = True
                        else:
                            # Tracked playlist was deleted by the user — recreate it.
                            logger.info(
                                f"Tracked playlist {tracked_id} for user '{u_name}' mix '{m_name}' "
                                f"returned 404 (deleted externally). Recreating."
                            )
                            playlist_id = await jf_client.create_playlist(m_name, u_id, selected_ids)
                            action_note = (
                                f"Recreated (tracked ID {tracked_id} was deleted) "
                                f"with {len(selected_ids)} tracks"
                            )
                            access_needs_fixing = True
                    else:
                        # No prior record — first time creating this mix for this user.
                        playlist_id = await jf_client.create_playlist(m_name, u_id, selected_ids)
                        action_note = f"Created new playlist with {len(selected_ids)} tracks"
                        access_needs_fixing = True

                    # Enforce private access (IsPublic=false) on every create/recreate
                    # and on updates of playlists that pre-date the fix.
                    # Belt-and-suspenders: IsPublic is set on create AND confirmed via
                    # UpdatePlaylist, because some Jellyfin versions ignore IsPublic
                    # on the create endpoint.
                    # userId is passed as a query param — required workaround for the
                    # Jellyfin bug where global API key auth resolves to Guid.Empty
                    # in GetPlaylistForUser, causing 400 (see Jellyfin issue #12092).
                    if access_needs_fixing:
                        try:
                            await jf_client.set_playlist_access(playlist_id, u_id, is_public=False)
                        except Exception as acc_err:
                            logger.warning(
                                f"Failed to set IsPublic=false on playlist {playlist_id} "
                                f"for user '{u_name}' mix '{m_name}': {acc_err}"
                            )

                    # Upload mix icon to Jellyfin playlist if available
                    icon_bytes, content_type = load_icon_bytes(mix.get("icon_path"))
                    if icon_bytes:
                        try:
                            await jf_client.set_playlist_image(playlist_id, icon_bytes, content_type)
                        except Exception as img_err:
                            logger.warning(f"Failed to push icon to playlist {playlist_id}: {img_err}")

                    # Update user playlist state & log entry
                    now_iso = datetime.now().isoformat()
                    with get_db(db_file) as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            INSERT INTO user_playlist_state (user_id, mix_key, jellyfin_playlist_id, last_generated_at, last_track_count, last_status)
                            VALUES (?, ?, ?, ?, ?, 'generated')
                            ON CONFLICT(user_id, mix_key) DO UPDATE SET
                                jellyfin_playlist_id = excluded.jellyfin_playlist_id,
                                last_generated_at = excluded.last_generated_at,
                                last_track_count = excluded.last_track_count,
                                last_status = excluded.last_status;
                            """,
                            (u_id, m_key, playlist_id, now_iso, len(selected_ids)),
                        )
                        cursor.execute(
                            """
                            INSERT INTO run_log_entries (run_id, user_id, username, mix_key, status, detail, track_count, created_at)
                            VALUES (?, ?, ?, ?, 'generated', ?, ?, ?);
                            """,
                            (
                                run_id,
                                u_id,
                                u_name,
                                m_key,
                                f"{action_note} (Method: {resolution_method})",
                                len(selected_ids),
                                now_iso,
                            ),
                        )
                    summary_stats["generated"] += 1

                except Exception as mix_err:
                    err_msg = f"Mix '{m_name}' failed for user '{u_name}': {str(mix_err)}"
                    logger.error(err_msg, exc_info=True)
                    with get_db(db_file) as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            INSERT INTO run_log_entries (run_id, user_id, username, mix_key, status, detail, track_count, created_at)
                            VALUES (?, ?, ?, ?, 'error', ?, 0, ?);
                            """,
                            (run_id, u_id, u_name, m_key, err_msg, datetime.now().isoformat()),
                        )
                    summary_stats["errors"] += 1

            # Update user's last_processed_at timestamp
            with get_db(db_file) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET last_processed_at = ? WHERE jellyfin_user_id = ?;",
                    (datetime.now().isoformat(), u_id),
                )

        # Determine overall run status
        finish_time = datetime.now()
        overall_status = "completed"
        if summary_stats["errors"] > 0:
            overall_status = "partial" if summary_stats["generated"] > 0 else "failed"

        with get_db(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE run_log
                SET finished_at = ?, status = ?, summary = ?
                WHERE run_id = ?;
                """,
                (finish_time.isoformat(), overall_status, json.dumps(summary_stats), run_id),
            )

        logger.info(f"Run {run_id} finished with status '{overall_status}': {summary_stats}")
        return {
            "run_id": run_id,
            "status": overall_status,
            "summary": summary_stats,
            "started_at": start_time.isoformat(),
            "finished_at": finish_time.isoformat(),
        }

    except Exception as fatal_err:
        finish_time = datetime.now()
        fatal_msg = f"Fatal generator run error: {str(fatal_err)}"
        logger.error(fatal_msg, exc_info=True)
        with get_db(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE run_log
                SET finished_at = ?, status = 'failed', summary = ?
                WHERE run_id = ?;
                """,
                (finish_time.isoformat(), json.dumps({"fatal_error": fatal_msg}), run_id),
            )
        return {
            "run_id": run_id,
            "status": "failed",
            "error": fatal_msg,
            "started_at": start_time.isoformat(),
            "finished_at": finish_time.isoformat(),
        }


async def push_all_mix_icons(db_file: Path | str = DB_PATH) -> dict:
    """Push configured mix icons immediately to all existing user playlists in Jellyfin."""
    settings = get_all_settings(db_file)
    jf_client = JellyfinClient(
        base_url=settings.get("jellyfin_url", ""),
        api_key=settings.get("jellyfin_api_key", ""),
        username=settings.get("jellyfin_username", ""),
        password=settings.get("jellyfin_password", ""),
    )

    mixes = get_mix_definitions(db_file)
    mix_map = {m["display_name"].strip().lower(): m for m in mixes}

    results = {"total_updated": 0, "errors": 0, "details": []}

    try:
        with get_db(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT jellyfin_user_id, username FROM users WHERE enabled = 1;")
            users = cursor.fetchall()

        for u in users:
            u_id = u["jellyfin_user_id"]
            u_name = u["username"]
            playlists = await jf_client.get_user_playlists(u_id)

            for pl in playlists:
                pl_name = pl["name"].strip().lower()
                if pl_name in mix_map:
                    mix = mix_map[pl_name]
                    icon_bytes, content_type = load_icon_bytes(mix.get("icon_path"))
                    if icon_bytes:
                        ok = await jf_client.set_playlist_image(pl["id"], icon_bytes, content_type)
                        if ok:
                            results["total_updated"] += 1
                            results["details"].append(f"Updated icon for {u_name}'s '{pl['name']}'")
                        else:
                            results["errors"] += 1
                            results["details"].append(f"Failed to set image for {u_name}'s '{pl['name']}'")
    except Exception as e:
        results["errors"] += 1
        results["details"].append(f"Push icon exception: {str(e)}")

    return results


async def fix_all_playlist_access(db_file: Path | str = DB_PATH) -> dict:
    """Retroactively enforce IsPublic=false and OpenAccess=false on every tracked playlist.

    For playlists where in-place access update succeeds (e.g. admin's own playlists),
    it updates them in-place and verifies OpenAccess: false.
    For non-admin playlists where in-place update returns 403 Forbidden (due to Jellyfin
    ownership enforcement), it deletes the exposed playlist via admin session, recreates
    it fresh via create_playlist (which sets OpenAccess: false / IsPublic: false), re-applies
    the mix icon, updates user_playlist_state with the new ID, and verifies OpenAccess: false.
    """
    settings = get_all_settings(db_file)
    jf_client = JellyfinClient(
        base_url=settings.get("jellyfin_url", ""),
        api_key=settings.get("jellyfin_api_key", ""),
        username=settings.get("jellyfin_username", ""),
        password=settings.get("jellyfin_password", ""),
    )

    mixes = get_mix_definitions(db_file)
    mix_map = {m["mix_key"]: m for m in mixes}

    results = {
        "total_fixed": 0,
        "already_gone": 0,
        "errors": 0,
        "details": [],
    }

    # Load every tracked playlist from the DB
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ups.user_id, u.username, ups.mix_key, ups.jellyfin_playlist_id
            FROM user_playlist_state ups
            JOIN users u ON u.jellyfin_user_id = ups.user_id
            WHERE ups.jellyfin_playlist_id IS NOT NULL;
            """
        )
        tracked = [dict(row) for row in cursor.fetchall()]

    logger.info(f"fix_all_playlist_access: inspecting & fixing {len(tracked)} tracked playlists.")

    for row in tracked:
        pl_id = row["jellyfin_playlist_id"]
        u_id = row["user_id"]
        u_name = row["username"]
        m_key = row["mix_key"]
        mix_def = mix_map.get(m_key, {})
        m_name = mix_def.get("display_name", f"{m_key.title()} Mix")
        label = f"{u_name} / {m_name} ({pl_id})"

        try:
            # 1. Fetch current live playlist details
            pl_data = None
            try:
                pl_data = await jf_client.get_playlist(pl_id)
            except Exception as get_err:
                if "404" in str(get_err) or "Not Found" in str(get_err):
                    results["already_gone"] += 1
                    results["details"].append(f"⚠️ Not found (already deleted): {label}")
                    logger.debug(f"fix_all_playlist_access: {label} returned 404, skipping.")
                    continue
                raise

            # 2. Check if already private
            if pl_data.get("OpenAccess") is False:
                results["total_fixed"] += 1
                results["details"].append(f"✅ Already private (OpenAccess: false): {label}")
                logger.info(f"fix_all_playlist_access: {label} already has OpenAccess=false")
                continue

            # 3. Try in-place update first (succeeds on admin's playlists)
            in_place_success = False
            try:
                await jf_client.set_playlist_access(pl_id, u_id, is_public=False)
                check_data = await jf_client.get_playlist(pl_id)
                if check_data.get("OpenAccess") is False:
                    in_place_success = True
                    results["total_fixed"] += 1
                    results["details"].append(f"✅ Updated in-place & verified private (OpenAccess: false): {label}")
                    logger.info(f"fix_all_playlist_access: in-place patch succeeded for {label}")
            except Exception as patch_err:
                logger.info(f"fix_all_playlist_access: in-place patch failed ({patch_err}); switching to delete+recreate for {label}")

            if in_place_success:
                continue

            # 4. In-place update failed (e.g. 403 Forbidden on non-admin user playlists).
            # Delete old public playlist and recreate fresh with OpenAccess: false.
            track_ids = pl_data.get("ItemIds", []) if pl_data else []
            logger.info(f"fix_all_playlist_access: deleting old public playlist {pl_id} for {label} ({len(track_ids)} tracks)")
            await jf_client.delete_playlist(pl_id)

            # Recreate with OpenAccess: false
            new_pl_id = await jf_client.create_playlist(m_name, u_id, track_ids)
            logger.info(f"fix_all_playlist_access: created fresh private playlist {new_pl_id} for {label}")

            # Reapply mix icon if configured
            icon_bytes, content_type = load_icon_bytes(mix_def.get("icon_path"))
            if icon_bytes:
                try:
                    await jf_client.set_playlist_image(new_pl_id, icon_bytes, content_type)
                except Exception as img_err:
                    logger.warning(f"Failed to reapply icon to {new_pl_id}: {img_err}")

            # Update DB with new playlist ID
            with get_db(db_file) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE user_playlist_state
                    SET jellyfin_playlist_id = ?
                    WHERE user_id = ? AND mix_key = ?;
                    """,
                    (new_pl_id, u_id, m_key),
                )

            # Verify new playlist has OpenAccess: false
            new_pl_data = await jf_client.get_playlist(new_pl_id)
            verified_open = new_pl_data.get("OpenAccess")
            if verified_open is False:
                results["total_fixed"] += 1
                results["details"].append(f"✅ Recreated & verified private (OpenAccess: false): {u_name} / {m_name} (New ID: {new_pl_id})")
            else:
                results["total_fixed"] += 1
                results["details"].append(f"✅ Recreated (OpenAccess: {verified_open}): {u_name} / {m_name} (New ID: {new_pl_id})")

        except Exception as e:
            err_str = str(e)
            results["errors"] += 1
            results["details"].append(f"❌ Error: {label} — {err_str}")
            logger.error(f"fix_all_playlist_access: failed to fix {label}: {e}", exc_info=True)

    logger.info(
        f"fix_all_playlist_access complete: "
        f"{results['total_fixed']} fixed, "
        f"{results['already_gone']} not found, "
        f"{results['errors']} errors."
    )
    return results

