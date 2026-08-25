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
        username: str | None = None,
        password: str | None = None,
        playback_db_path: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.username = username.strip() if username else ""
        self.password = password if password else ""
        self.playback_db_path = playback_db_path.strip() if playback_db_path else None
        self.timeout = timeout
        self._session_token: str | None = None

    def _get_headers(self) -> dict[str, str]:
        """Headers authenticated with the static API key."""
        return {
            "X-Emby-Token": self.api_key,
            "Authorization": f'MediaBrowser Client="Jellyfin Smart Playlists", Device="Server", DeviceId="jellyfin-smart-playlists", Version="1.0.0", Token="{self.api_key}"',
            "Accept": "application/json",
        }

    async def get_session_token(self, force_refresh: bool = False) -> str:
        """Obtain or return cached session AccessToken for Jellyfin admin user.

        Authenticates via POST /Users/AuthenticateByName to obtain a real session token.
        Falls back to api_key if username is not configured.
        """
        if self._session_token and not force_refresh:
            return self._session_token

        if not self.username:
            return self.api_key

        auth_header = 'MediaBrowser Client="Jellyfin Smart Playlists", Device="Server", DeviceId="jellyfin-smart-playlists", Version="1.0.0"'
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/Users/AuthenticateByName",
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={"Username": self.username, "Pw": self.password},
            )
            if not resp.is_success:
                logger.error(f"Failed to authenticate as Jellyfin admin '{self.username}': HTTP {resp.status_code} — {resp.text}")
                resp.raise_for_status()

            data = resp.json()
            token = data.get("AccessToken")
            if not token:
                raise ValueError("No AccessToken returned from /Users/AuthenticateByName")
            self._session_token = token
            logger.info(f"Successfully authenticated session token for Jellyfin admin '{self.username}'")
            return self._session_token

    async def _get_session_headers(self) -> dict[str, str]:
        """Headers authenticated with the user session AccessToken."""
        token = await self.get_session_token()
        return {
            "Authorization": f'MediaBrowser Client="Jellyfin Smart Playlists", Device="Server", DeviceId="jellyfin-smart-playlists", Version="1.0.0", Token="{token}"',
            "X-Emby-Token": token,
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
            "admin_authenticated": False,
            "admin_username": self.username or None,
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
                    logger.debug(f"Playback reporting endpoint check failed: {e}")

                # 5. Check Admin User Authentication if username configured
                if self.username:
                    try:
                        await self.get_session_token(force_refresh=True)
                        result["admin_authenticated"] = True
                    except Exception as auth_err:
                        result["admin_authenticated"] = False
                        result["admin_auth_error"] = str(auth_err)
                        logger.warning(f"Admin session authentication check failed: {auth_err}")

        except httpx.ConnectError:
            result["error"] = f"Could not connect to Jellyfin server at {self.base_url}. Check URL and network connectivity."
        except Exception as e:
            result["error"] = f"Jellyfin connection error: {str(e)}"

        return result

    async def get_users(self) -> list[dict]:
        """Fetch all non-disabled Jellyfin users."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/Users",
                headers=self._get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            users = []
            for u in data:
                policy = u.get("Policy", {})
                is_disabled = policy.get("IsDisabled", False)
                users.append({
                    "id": u["Id"],
                    "name": u["Name"],
                    "is_disabled": is_disabled,
                })
            return users

    async def get_all_audio_items(self) -> list[dict]:
        """Fetch all audio tracks with metadata from Jellyfin library."""
        tracks = []
        start_index = 0
        limit = 500

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while True:
                resp = await client.get(
                    f"{self.base_url}/Items",
                    headers=self._get_headers(),
                    params={
                        "Recursive": "true",
                        "IncludeItemTypes": "Audio",
                        "Fields": "Genres,ProductionYear,RunTimeTicks,SongInfos,MediaSources",
                        "StartIndex": str(start_index),
                        "Limit": str(limit),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("Items", [])
                total = data.get("TotalRecordCount", len(items))

                for it in items:
                    genres = it.get("Genres", [])
                    bpm = None
                    song_infos = it.get("SongInfos")
                    if isinstance(song_infos, list) and song_infos:
                        bpm = song_infos[0].get("Bpm")
                    if bpm is None:
                        media_sources = it.get("MediaSources", [])
                        for ms in media_sources:
                            for stream in ms.get("MediaStreams", []):
                                if stream.get("Type") == "Audio" and stream.get("Bpm"):
                                    bpm = stream.get("Bpm")
                                    break
                            if bpm is not None:
                                break

                    tracks.append({
                        "item_id": it["Id"],
                        "title": it.get("Name", "Unknown Title"),
                        "artist": it.get("AlbumArtist") or it.get("Artists", ["Unknown Artist"])[0] if it.get("Artists") else "Unknown Artist",
                        "album": it.get("Album", "Unknown Album"),
                        "genres": genres,
                        "production_year": it.get("ProductionYear"),
                        "duration_ticks": it.get("RunTimeTicks", 0),
                        "bpm": bpm,
                    })

                start_index += len(items)
                if start_index >= total or not items:
                    break

        return tracks

    async def get_playback_activity(
        self,
        user_id: str,
        since_iso: str | None = None,
    ) -> list[dict]:
        """Fetch playback history events for a user."""
        events = []

        # Tier 1: Try Playback Reporting plugin API
        try:
            safe_user_id = user_id.replace("'", "''")
            sql_query = f"SELECT ItemId, DateCreated FROM PlaybackActivity WHERE UserId = '{safe_user_id}'"
            if since_iso:
                safe_since = since_iso.replace("'", "''")
                sql_query += f" AND DateCreated > '{safe_since}'"
            sql_query += " ORDER BY DateCreated DESC;"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/user_usage_stats/submit_custom_query",
                    headers=self._get_headers(),
                    json={"CustomQueryString": sql_query, "ReplaceUserId": False},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    rows = data.get("results", data) if isinstance(data, dict) else data
                    if isinstance(rows, list):
                        for r in rows:
                            item_id = r.get("ItemId") or r.get("item_id") or r.get("0")
                            date_str = r.get("DateCreated") or r.get("date_created") or r.get("1")
                            if item_id:
                                events.append({
                                    "item_id": str(item_id),
                                    "timestamp": str(date_str) if date_str else datetime.now().isoformat(),
                                })
                        return events
        except Exception as e:
            logger.debug(f"Playback Reporting plugin API query failed: {e}")

        # Tier 2: Direct SQLite file reading
        if self.playback_db_path and Path(self.playback_db_path).exists():
            try:
                conn = sqlite3.connect(self.playback_db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if since_iso:
                    cursor.execute(
                        "SELECT ItemId, DateCreated FROM PlaybackActivity WHERE UserId = ? AND DateCreated > ? ORDER BY DateCreated DESC;",
                        (user_id, since_iso),
                    )
                else:
                    cursor.execute(
                        "SELECT ItemId, DateCreated FROM PlaybackActivity WHERE UserId = ? ORDER BY DateCreated DESC;",
                        (user_id,),
                    )
                for row in cursor.fetchall():
                    events.append({
                        "item_id": row["ItemId"],
                        "timestamp": row["DateCreated"],
                    })
                conn.close()
                return events
            except Exception as e:
                logger.warning(f"Direct Playback Reporting SQLite read failed: {e}")

        # Tier 3: Native Jellyfin UserData fallback
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params = {
                    "UserId": user_id,
                    "Recursive": "true",
                    "IncludeItemTypes": "Audio",
                    "IsPlayed": "true",
                    "Fields": "UserData",
                }
                resp = await client.get(
                    f"{self.base_url}/Users/{user_id}/Items",
                    headers=self._get_headers(),
                    params=params,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for it in data.get("Items", []):
                        ud = it.get("UserData", {})
                        play_count = ud.get("PlayCount", 0)
                        last_played = ud.get("LastPlayedDate")
                        if play_count > 0:
                            for _ in range(min(play_count, 10)):
                                events.append({
                                    "item_id": it["Id"],
                                    "timestamp": last_played or datetime.now().isoformat(),
                                })
                    return events
        except Exception as e:
            logger.error(f"Fallback UserData query failed for user {user_id}: {e}")

        return events

    async def get_user_playlists(self, user_id: str) -> list[dict]:
        """Fetch all playlists owned by or visible to a user."""
        headers = await self._get_session_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/Users/{user_id}/Items",
                headers=headers,
                params={
                    "IncludeItemTypes": "Playlist",
                    "Recursive": "true",
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
        headers = await self._get_session_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(
                    f"{self.base_url}/Playlists/{playlist_id}/Items",
                    headers=headers,
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

    async def get_playlist(self, playlist_id: str) -> dict:
        """Retrieve full details of a playlist including OpenAccess, Shares, and ItemIds."""
        headers = await self._get_session_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/Playlists/{playlist_id}",
                headers=headers,
            )
            if resp.status_code == 401 and self.username:
                await self.get_session_token(force_refresh=True)
                headers = await self._get_session_headers()
                resp = await client.get(
                    f"{self.base_url}/Playlists/{playlist_id}",
                    headers=headers,
                )
            if not resp.is_success:
                logger.error(f"get_playlist failed for {playlist_id} ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def create_playlist(self, name: str, user_id: str, item_ids: list[str]) -> str:
        """Create a new playlist for the user and populate it.

        Uses admin session token and explicitly sets IsPublic=False / OpenAccess=False
        to prevent server-wide public visibility.
        """
        headers = await self._get_session_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            payload = {
                "Name": name,
                "Ids": item_ids,
                "UserId": user_id,
                "MediaType": "Audio",
                "IsPublic": False,
                "OpenAccess": False,
            }
            resp = await client.post(
                f"{self.base_url}/Playlists",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 401 and self.username:
                logger.warning("Session token expired during create_playlist. Refreshing token and retrying...")
                await self.get_session_token(force_refresh=True)
                headers = await self._get_session_headers()
                resp = await client.post(
                    f"{self.base_url}/Playlists",
                    headers=headers,
                    json=payload,
                )
            if not resp.is_success:
                logger.error(f"create_playlist failed for '{name}' user='{user_id}' ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            data = resp.json()
            return data["Id"]

    async def set_playlist_access(self, playlist_id: str, user_id: str, is_public: bool = False) -> None:
        """Set the public/private access flag on an existing playlist.

        Uses POST /Playlists/{playlistId} (UpdatePlaylist) with admin session token.
        Explicitly sets IsPublic=False / OpenAccess=False.
        """
        headers = await self._get_session_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/Playlists/{playlist_id}",
                headers=headers,
                params={"userId": user_id},
                json={"IsPublic": is_public, "OpenAccess": is_public},
            )
            if resp.status_code == 401 and self.username:
                logger.warning(f"Session token expired during set_playlist_access for {playlist_id}. Refreshing token and retrying...")
                await self.get_session_token(force_refresh=True)
                headers = await self._get_session_headers()
                resp = await client.post(
                    f"{self.base_url}/Playlists/{playlist_id}",
                    headers=headers,
                    params={"userId": user_id},
                    json={"IsPublic": is_public, "OpenAccess": is_public},
                )
            if not resp.is_success:
                logger.error(
                    f"set_playlist_access failed for playlist={playlist_id} user={user_id}: "
                    f"HTTP {resp.status_code} — {resp.text}"
                )
            resp.raise_for_status()

    async def update_playlist_items(self, playlist_id: str, user_id: str, item_ids: list[str]) -> None:
        """Replace the items in an existing playlist."""
        headers = await self._get_session_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # 1. Fetch current items in the playlist
            get_resp = await client.get(
                f"{self.base_url}/Playlists/{playlist_id}/Items",
                headers=headers,
                params={"UserId": user_id},
            )
            if get_resp.status_code == 401 and self.username:
                await self.get_session_token(force_refresh=True)
                headers = await self._get_session_headers()
                get_resp = await client.get(
                    f"{self.base_url}/Playlists/{playlist_id}/Items",
                    headers=headers,
                    params={"UserId": user_id},
                )
            get_resp.raise_for_status()
            current_items = get_resp.json().get("Items", [])

            # 2. Delete existing items
            if current_items:
                entry_ids = [
                    item.get("PlaylistItemId") or item.get("Id")
                    for item in current_items
                    if item.get("PlaylistItemId") or item.get("Id")
                ]
                if entry_ids:
                    chunk_size = 50
                    for i in range(0, len(entry_ids), chunk_size):
                        chunk = entry_ids[i:i + chunk_size]
                        del_resp = await client.delete(
                            f"{self.base_url}/Playlists/{playlist_id}/Items",
                            headers=headers,
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
                        headers=headers,
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
        headers = await self._get_session_headers()
        headers["Content-Type"] = content_type

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/Items/{playlist_id}/Images/Primary",
                    headers=headers,
                    content=image_bytes,
                )
                if resp.status_code == 401 and self.username:
                    await self.get_session_token(force_refresh=True)
                    headers = await self._get_session_headers()
                    headers["Content-Type"] = content_type
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
