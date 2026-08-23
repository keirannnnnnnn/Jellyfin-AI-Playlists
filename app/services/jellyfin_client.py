import sqlite3
import logging
from pathlib import Path
import httpx
from datetime import datetime

logger = logging.getLogger("jellyfin_playlists.jellyfin")


class JellyfinClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        playback_db_path: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.playback_db_path = playback_db_path.strip() if playback_db_path else None
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        return {
            "X-Emby-Token": self.api_key,
            "Authorization": f'MediaBrowser Client="Jellyfin Smart Playlists", Device="Server", DeviceId="jellyfin-smart-playlists", Version="1.0.0", Token="{self.api_key}"',
            "Accept": "application/json",
        }

    async def test_connection(self) -> dict:
        """Test connection to Jellyfin server and diagnose capabilities."""
        result = {
            "connected": False,
            "server_name": None,
            "version": None,
            "user_count": 0,
            "audio_count": 0,
            "playback_reporting_available": False,
            "playback_reporting_mode": "none",
            "error": None,
        }

        if not self.base_url or not self.api_key:
            result["error"] = "Jellyfin URL and API key must be configured."
            return result

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # 1. Test System Info
                resp = await client.get(
                    f"{self.base_url}/System/Info",
                    headers=self._get_headers(),
                )
                if resp.status_code == 401:
                    result["error"] = "Invalid Jellyfin API Key (HTTP 401 Unauthorized)."
                    return result
                resp.raise_for_status()
                info = resp.json()
                result["connected"] = True
                result["server_name"] = info.get("ServerName", "Jellyfin Server")
                result["version"] = info.get("Version", "Unknown")

                # 2. Get User count
                users_resp = await client.get(
                    f"{self.base_url}/Users",
                    headers=self._get_headers(),
                )
                if users_resp.status_code == 200:
                    users_data = users_resp.json()
                    result["user_count"] = len(users_data)

                # 3. Get Audio count
                audio_resp = await client.get(
                    f"{self.base_url}/Items",
                    headers=self._get_headers(),
                    params={
                        "Recursive": "true",
                        "IncludeItemTypes": "Audio",
                        "Limit": "1",
                    },
                )
                if audio_resp.status_code == 200:
                    audio_data = audio_resp.json()
                    result["audio_count"] = audio_data.get("TotalRecordCount", 0)

                # 4. Check Playback Reporting API endpoint
                try:
                    query_payload = {
                        "CustomQueryString": "SELECT 1;",
                        "ReplaceUserId": False,
                    }
                    pr_resp = await client.post(
                        f"{self.base_url}/user_usage_stats/submit_custom_query",
                        headers=self._get_headers(),
                        json=query_payload,
                    )
                    if pr_resp.status_code == 200:
                        result["playback_reporting_available"] = True
                        result["playback_reporting_mode"] = "plugin_api"
                except Exception as e:
                    logger.debug(f"Playback reporting plugin API test failed: {e}")

                # 5. Check direct SQLite if configured
                if not result["playback_reporting_available"] and self.playback_db_path:
                    db_p = Path(self.playback_db_path)
                    if db_p.exists() and db_p.is_file():
                        result["playback_reporting_available"] = True
                        result["playback_reporting_mode"] = "direct_sqlite"

                if not result["playback_reporting_available"]:
                    result["playback_reporting_mode"] = "userdata_fallback"

        except httpx.ConnectError:
            result["error"] = f"Could not connect to Jellyfin at {self.base_url}. Ensure the server is running and reachable."
        except Exception as e:
            result["error"] = f"Connection error: {str(e)}"

        return result

    async def get_users(self) -> list[dict]:
        """Fetch all users from Jellyfin."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/Users",
                headers=self._get_headers(),
            )
            resp.raise_for_status()
            users = resp.json()
            return [
                {
                    "id": u["Id"],
                    "name": u["Name"],
                    "is_disabled": u.get("Policy", {}).get("IsDisabled", False),
                }
                for u in users
            ]

    async def get_all_audio_items(self) -> list[dict]:
        """Fetch all audio items with full metadata from Jellyfin library."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{self.base_url}/Items",
                headers=self._get_headers(),
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Audio",
                    "Fields": "Genres,ProductionYear,Artists,Album,Tags,SongInfos,MediaStreams,DateCreated,PlayCount,UserData",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("Items", [])
            parsed = []

            for item in items:
                # Artists extraction
                artists = item.get("Artists", [])
                if not artists:
                    artist_name = item.get("AlbumArtist") or (item.get("ArtistItems", [{}])[0].get("Name") if item.get("ArtistItems") else "")
                    if artist_name:
                        artists = [artist_name]

                # BPM extraction
                bpm = None
                if "Bpm" in item and item["Bpm"] is not None:
                    try:
                        bpm = float(item["Bpm"])
                    except (ValueError, TypeError):
                        pass

                # If BPM is not direct field, check Tags
                if bpm is None:
                    for tag in item.get("Tags", []):
                        if "bpm" in tag.lower():
                            try:
                                bpm = float("".join(c for c in tag if c.isdigit() or c == "."))
                                break
                            except Exception:
                                pass

                # Production year
                prod_year = item.get("ProductionYear")
                if not prod_year and item.get("PremiereDate"):
                    try:
                        prod_year = int(item["PremiereDate"][:4])
                    except Exception:
                        pass

                parsed.append({
                    "item_id": item["Id"],
                    "title": item.get("Name", "Unknown Track"),
                    "artist": ", ".join(artists) if artists else "Unknown Artist",
                    "album": item.get("Album", ""),
                    "genres": item.get("Genres", []),
                    "production_year": prod_year,
                    "duration_ticks": item.get("RunTimeTicks", 0),
                    "bpm": bpm,
                    "date_created": item.get("DateCreated"),
                })
            return parsed

    async def get_playback_activity(
        self,
        user_id: str,
        since_iso: str | None = None,
    ) -> list[dict]:
        """Fetch user playback activity events from Playback Reporting plugin (or DB or fallback)."""
        # Try 1: Playback Reporting endpoint
        # The submit_custom_query endpoint only accepts a raw SQL string and does not
        # support native bind parameters.  We sanitise the two values we interpolate
        # (user_id from Jellyfin /Users, since_iso from our own DB) by escaping any
        # embedded single-quotes.  These values never come from external user input.
        try:
            safe_user_id = user_id.replace("'", "''")
            query = (
                "SELECT DateCreated, UserId, ItemId, PlayDuration "
                "FROM PlaybackActivity "
                "WHERE ItemType = 'Audio' "
                f"AND UserId = '{safe_user_id}'"
            )
            if since_iso:
                safe_since = since_iso.replace("'", "''")
                query += f" AND DateCreated > '{safe_since}'"
            query += " ORDER BY DateCreated DESC;"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/user_usage_stats/submit_custom_query",
                    headers=self._get_headers(),
                    json={"CustomQueryString": query, "ReplaceUserId": False},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Playback Reporting returns list of rows or dict with results
                    # Schema typically has columns & rows: {"colums": [...], "results": [[...]]} or [{"DateCreated":...}]
                    results = []
                    if isinstance(data, list):
                        for row in data:
                            results.append({
                                "date_created": row.get("DateCreated"),
                                "item_id": row.get("ItemId"),
                                "play_duration": row.get("PlayDuration", 0),
                            })
                        return results
                    elif isinstance(data, dict) and "results" in data:
                        cols = [c.lower() for c in data.get("columns", ["datecreated", "userid", "itemid", "playduration"])]
                        item_id_idx = cols.index("itemid") if "itemid" in cols else 2
                        date_idx = cols.index("datecreated") if "datecreated" in cols else 0
                        dur_idx = cols.index("playduration") if "playduration" in cols else 3

                        for row in data["results"]:
                            if len(row) > max(item_id_idx, date_idx):
                                results.append({
                                    "date_created": row[date_idx],
                                    "item_id": row[item_id_idx],
                                    "play_duration": row[dur_idx] if len(row) > dur_idx else 0,
                                })
                        return results
        except Exception as e:
            logger.debug(f"Playback Reporting API query failed, trying next method: {e}")

        # Try 2: Direct SQLite file — always fully parameterized
        if self.playback_db_path and Path(self.playback_db_path).exists():
            try:
                conn = sqlite3.connect(self.playback_db_path)
                cursor = conn.cursor()
                sql = "SELECT DateCreated, ItemId, PlayDuration FROM PlaybackActivity WHERE ItemType = 'Audio' AND UserId = ?"
                params = [user_id]
                if since_iso:
                    sql += " AND DateCreated > ?"
                    params.append(since_iso)
                sql += " ORDER BY DateCreated DESC;"
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                conn.close()
                return [
                    {
                        "date_created": r[0],
                        "item_id": r[1],
                        "play_duration": r[2],
                    }
                    for r in rows
                ]
            except Exception as e:
                logger.debug(f"Direct Playback DB read failed: {e}")

        # Try 3: Fallback to Jellyfin UserData on items
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/Users/{user_id}/Items",
                    headers=self._get_headers(),
                    params={
                        "Recursive": "true",
                        "IncludeItemTypes": "Audio",
                        "Fields": "UserData,PlayCount",
                        "IsPlayed": "true",
                    },
                )
                if resp.status_code == 200:
                    items = resp.json().get("Items", [])
                    fallback_results = []
                    for item in items:
                        ud = item.get("UserData", {})
                        last_played = ud.get("LastPlayedDate")
                        play_count = ud.get("PlayCount", 1)
                        if last_played:
                            if not since_iso or last_played > since_iso:
                                for _ in range(min(play_count, 10)):
                                    fallback_results.append({
                                        "date_created": last_played,
                                        "item_id": item["Id"],
                                        "play_duration": 0,
                                    })
                    return fallback_results
        except Exception as e:
            logger.error(f"Fallback UserData query failed: {e}")

        return []

    async def get_user_playlists(self, user_id: str) -> list[dict]:
        """Fetch playlists belonging to a specific user."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/Users/{user_id}/Items",
                headers=self._get_headers(),
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": "Playlist",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "id": item["Id"],
                    "name": item.get("Name", ""),
                }
                for item in data.get("Items", [])
            ]

    async def playlist_exists(self, playlist_id: str, user_id: str) -> bool:
        """Check whether a tracked playlist ID still exists in Jellyfin (returns False on 404)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(
                    f"{self.base_url}/Playlists/{playlist_id}/Items",
                    headers=self._get_headers(),
                    params={"UserId": user_id, "Limit": "1"},
                )
                if resp.status_code == 404:
                    return False
                resp.raise_for_status()
                return True
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return False
                raise

    async def create_playlist(self, name: str, user_id: str, item_ids: list[str]) -> str:
        """Create a new playlist for the user and populate it.

        Sends a JSON body only — no duplicate query params.  The POST /Playlists
        contract (verified against Jellyfin 10.x Swagger) accepts:
          Name, Ids (array), UserId, MediaType in the request body.
        IsPublic is not a documented field on this endpoint and is omitted.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # JSON body only — no duplicate query params.
            # POST /Playlists accepts Name, Ids, UserId, MediaType in the request body.
            # IsPublic is not documented on this endpoint and is omitted.
            payload = {
                "Name": name,
                "Ids": item_ids,
                "UserId": user_id,
                "MediaType": "Audio",
            }
            resp = await client.post(
                f"{self.base_url}/Playlists",
                headers=self._get_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["Id"]

    async def update_playlist_items(self, playlist_id: str, user_id: str, item_ids: list[str]) -> None:
        """Replace the items in an existing playlist."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # 1. Fetch current items in the playlist
            get_resp = await client.get(
                f"{self.base_url}/Playlists/{playlist_id}/Items",
                headers=self._get_headers(),
                params={"UserId": user_id},
            )
            get_resp.raise_for_status()
            current_items = get_resp.json().get("Items", [])

            # 2. Delete existing items
            if current_items:
                # Jellyfin items in playlist have PlaylistItemId
                entry_ids = [
                    item.get("PlaylistItemId") or item.get("Id")
                    for item in current_items
                    if item.get("PlaylistItemId") or item.get("Id")
                ]
                if entry_ids:
                    # Remove in chunks if necessary
                    chunk_size = 50
                    for i in range(0, len(entry_ids), chunk_size):
                        chunk = entry_ids[i:i + chunk_size]
                        del_resp = await client.delete(
                            f"{self.base_url}/Playlists/{playlist_id}/Items",
                            headers=self._get_headers(),
                            params={"EntryIds": ",".join(chunk)},
                        )
                        del_resp.raise_for_status()

            # 3. Add new items
            if item_ids:
                chunk_size = 50
                for i in range(0, len(item_ids), chunk_size):
                    chunk = item_ids[i:i + chunk_size]
                    add_resp = await client.post(
                        f"{self.base_url}/Playlists/{playlist_id}/Items",
                        headers=self._get_headers(),
                        params={
                            "Ids": ",".join(chunk),
                            "UserId": user_id,
                        },
                    )
                    add_resp.raise_for_status()

    async def set_playlist_image(
        self,
        playlist_id: str,
        image_bytes: bytes,
        content_type: str = "image/png",
    ) -> bool:
        """Upload custom icon image as Primary image for the playlist."""
        headers = self._get_headers()
        headers["Content-Type"] = content_type

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/Items/{playlist_id}/Images/Primary",
                    headers=headers,
                    content=image_bytes,
                )
                if resp.status_code in (200, 204):
                    return True
                logger.warning(f"Failed to upload playlist image ({resp.status_code}): {resp.text}")
                return False
            except Exception as e:
                logger.error(f"Error setting playlist image for {playlist_id}: {e}")
                return False
