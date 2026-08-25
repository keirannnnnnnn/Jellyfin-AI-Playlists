#!/usr/bin/env python3
"""
Audit Playlist IDs: Read-Only Diagnostic Script

Compares every tracked playlist in user_playlist_state against the real, live
playlists returned by Jellyfin for each user.

Does NOT modify, create, or delete anything in either the database or Jellyfin.

Usage:
  python scripts/audit_playlist_ids.py
  python scripts/audit_playlist_ids.py --url https://movies.shoreline.je --api-key <KEY> --username <ADMIN> --password <PW>
"""

import sys
import os
import argparse
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_db, get_all_settings, get_mix_definitions, DB_PATH
from app.services.jellyfin_client import JellyfinClient


async def run_audit(url: str, api_key: str, username: str, password: str, db_file: Path | str):
    print("=" * 80)
    print("  JELLYFIN PLAYLIST TRACKING AUDIT (READ-ONLY)")
    print("=" * 80)

    # 1. Initialize Client & Authenticate
    client = JellyfinClient(
        base_url=url,
        api_key=api_key,
        username=username,
        password=password,
    )

    print(f"Jellyfin URL:    {url}")
    print(f"Admin Username:  {username or '(none configured)'}")
    print(f"Database File:   {db_file}\n")

    print("[1/3] Testing Connection & Authenticating Admin Session...")
    conn_info = await client.test_connection()
    if not conn_info.get("connected"):
        print(f"❌ Connection Failed: {conn_info.get('error')}")
        return

    print(f"  ✓ Connected to: {conn_info.get('server_name')} (v{conn_info.get('version')})")
    if username:
        if conn_info.get("admin_authenticated"):
            print(f"  ✓ Admin Session: Authenticated as '{username}' (Session token active)")
        else:
            print(f"  ⚠️ Admin Session Auth Failed: {conn_info.get('admin_auth_error')}")
    else:
        print("  ⚠️ No admin username configured; using API key only.")

    # 2. Load DB Records
    print("\n[2/3] Loading Database Records...")
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT jellyfin_user_id, username, enabled FROM users ORDER BY username;")
        users = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT user_id, mix_key, jellyfin_playlist_id, last_generated_at, last_status FROM user_playlist_state;")
        states = [dict(r) for r in cursor.fetchall()]

    mix_defs = get_mix_definitions(db_file)
    mix_map = {m["mix_key"]: m for m in mix_defs}

    # Group states by user_id
    user_state_map = {}
    for st in states:
        user_state_map.setdefault(st["user_id"], {})[st["mix_key"]] = st

    print(f"  ✓ Found {len(users)} users in database.")
    print(f"  ✓ Found {len(mix_defs)} mix definitions.")
    print(f"  ✓ Found {len(states)} tracked playlist state entries.\n")

    # 3. Audit Per User
    print("[3/3] Inspecting Live Jellyfin State Per User...")
    print("=" * 80)

    total_evaluated = 0
    total_match_private = 0
    total_match_public = 0
    total_stale_id = 0
    total_multiple_found = 0
    total_missing_in_jf = 0
    total_untracked_in_db = 0

    for u in users:
        u_id = u["jellyfin_user_id"]
        u_name = u["username"]
        u_enabled = "Enabled" if u.get("enabled") else "Disabled"
        u_states = user_state_map.get(u_id, {})

        print(f"\n👤 USER: {u_name} ({u_id}) — [{u_enabled}]")
        print("-" * 80)

        # Fetch all live playlists owned/visible for this user in Jellyfin
        try:
            live_playlists = await client.get_user_playlists(u_id)
        except Exception as e:
            print(f"  ❌ Failed to fetch live playlists for {u_name}: {e}")
            continue

        # Group live playlists by name (normalized lowercase)
        live_by_name: dict[str, list[dict]] = {}
        for lp in live_playlists:
            norm_name = lp.get("name", "").strip().lower()
            live_by_name.setdefault(norm_name, []).append(lp)

        # Inspect each defined mix
        for mix_key, mix in mix_map.items():
            total_evaluated += 1
            m_name = mix.get("display_name", mix_key)
            norm_mix_name = m_name.strip().lower()

            st = u_states.get(mix_key, {})
            tracked_id = st.get("jellyfin_playlist_id")
            live_matches = live_by_name.get(norm_mix_name, [])

            # Fetch details for tracked_id if present
            tracked_info = None
            if tracked_id:
                try:
                    tracked_info = await client.get_playlist(tracked_id)
                except Exception as e:
                    if "404" in str(e) or "Not Found" in str(e):
                        tracked_info = "404_NOT_FOUND"
                    else:
                        tracked_info = f"ERROR: {str(e)}"

            # Fetch details for all live matches
            live_details = []
            for lm in live_matches:
                lm_id = lm["id"]
                try:
                    lm_data = await client.get_playlist(lm_id)
                    open_access = lm_data.get("OpenAccess")
                    item_count = len(lm_data.get("ItemIds", []))
                    live_details.append({
                        "id": lm_id,
                        "name": lm.get("name"),
                        "open_access": open_access,
                        "track_count": item_count,
                    })
                except Exception as e:
                    live_details.append({
                        "id": lm_id,
                        "name": lm.get("name"),
                        "error": str(e),
                    })

            # Determine verdict
            verdict = ""
            details_str = ""

            if len(live_matches) == 0 and not tracked_id:
                verdict = "⚪ NOT_CREATED"
                details_str = "No DB record & not in Jellyfin"
            elif len(live_matches) == 0 and tracked_id:
                if tracked_info == "404_NOT_FOUND":
                    verdict = "❌ TRACKED_404_MISSING"
                    details_str = f"DB points to dead ID {tracked_id} (404); not in Jellyfin"
                    total_missing_in_jf += 1
                else:
                    verdict = "⚠️ TRACKED_DIFFERENT_NAME"
                    details_str = f"DB ID {tracked_id} exists but not named '{m_name}'"
            elif len(live_matches) == 1:
                lm = live_details[0]
                live_id = lm["id"]
                open_acc = lm.get("open_access")
                tracks = lm.get("track_count", 0)

                if tracked_id == live_id:
                    if open_acc is False:
                        verdict = "✅ MATCH_PRIVATE"
                        details_str = f"ID: {live_id} | OpenAccess: False | Tracks: {tracks}"
                        total_match_private += 1
                    else:
                        verdict = "⚠️ MATCH_PUBLIC"
                        details_str = f"ID: {live_id} | OpenAccess: True (Public!) | Tracks: {tracks}"
                        total_match_public += 1
                elif not tracked_id:
                    verdict = "⚠️ UNTRACKED_IN_DB"
                    details_str = f"Live ID: {live_id} (OpenAccess: {open_acc}) but not in DB"
                    total_untracked_in_db += 1
                else:
                    # tracked_id != live_id
                    verdict = "🔄 STALE_DB_ID"
                    tracked_status = "404 (Deleted)" if tracked_info == "404_NOT_FOUND" else f"Live (OpenAccess: {tracked_info.get('OpenAccess') if isinstance(tracked_info, dict) else '?'})"
                    details_str = (
                        f"DB has stale ID: {tracked_id} [{tracked_status}] ➔ Real Live ID: {live_id} "
                        f"[OpenAccess: {open_acc}, Tracks: {tracks}]"
                    )
                    total_stale_id += 1
            else:
                # Multiple playlists with same name found
                verdict = "⚡ MULTIPLE_FOUND"
                match_summary = ", ".join([f"{m['id']} (OpenAccess: {m.get('open_access')})" for m in live_details])
                details_str = f"Found {len(live_matches)} playlists named '{m_name}': {match_summary}"
                total_multiple_found += 1

            print(f"  [{mix_key.upper():<10}] {m_name:<22} : {verdict}")
            print(f"                 └─ {details_str}")

        # Check for any rogue/extra playlists that don't match known mix names
        known_norm_names = {mix.get("display_name", "").strip().lower() for mix in mix_defs}
        rogue_playlists = [p for p in live_playlists if p.get("name", "").strip().lower() not in known_norm_names]
        if rogue_playlists:
            print(f"\n  ℹ️ Other/Unrecognized Playlists in Jellyfin for {u_name}:")
            for rp in rogue_playlists:
                print(f"     • '{rp.get('name')}' (ID: {rp.get('id')})")

    # 4. Summary Table
    print("\n" + "=" * 80)
    print("  AUDIT SUMMARY")
    print("=" * 80)
    print(f"  Total Mix Slots Evaluated:        {total_evaluated}")
    print(f"  ✅ Correct & Verified Private:    {total_match_private}")
    print(f"  ⚠️ Matching but Public:           {total_match_public}")
    print(f"  🔄 Stale DB IDs (Needs Re-link):  {total_stale_id}")
    print(f"  ⚡ Multiple Candidates in JF:     {total_multiple_found}")
    print(f"  ❌ Dead Tracked IDs (Missing):    {total_missing_in_jf}")
    print(f"  ⚠️ Untracked in DB:               {total_untracked_in_db}")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Audit Jellyfin playlist tracking state against live server.")
    parser.add_argument("--url", default=None, help="Jellyfin Server URL (e.g. https://movies.shoreline.je)")
    parser.add_argument("--api-key", default=None, help="Jellyfin Admin API Key")
    parser.add_argument("--username", default=None, help="Jellyfin Admin Username")
    parser.add_argument("--password", default=None, help="Jellyfin Admin Password")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to SQLite database file")

    args = parser.parse_args()

    db_path = Path(args.db)
    settings = get_all_settings(db_path) if db_path.exists() else {}

    url = args.url or settings.get("jellyfin_url", "http://127.0.0.1:8096")
    api_key = args.api_key or settings.get("jellyfin_api_key", "")
    username = args.username or settings.get("jellyfin_username", "")
    password = args.password or settings.get("jellyfin_password", "")

    if not url or not api_key:
        print("Error: Jellyfin URL and API Key must be supplied via arguments or configured in DB.")
        sys.exit(1)

    asyncio.run(run_audit(url, api_key, username, password, db_path))


if __name__ == "__main__":
    main()
