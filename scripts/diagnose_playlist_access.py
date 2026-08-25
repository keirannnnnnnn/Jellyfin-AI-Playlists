import asyncio, sys
import httpx

async def main():
    if len(sys.argv) < 3:
        print("Usage: python diagnose_playlist_access.py <jellyfin_url> <api_key> [playlist_id]")
        print("e.g.:  python diagnose_playlist_access.py http://localhost:8096 abc123def playlist-uuid-here")
        return

    base = sys.argv[1].rstrip("/")
    key  = sys.argv[2]
    pl_id = sys.argv[3] if len(sys.argv) > 3 else None

    hdrs = {
        "X-Emby-Token": key,
        "Authorization": f'''MediaBrowser Client="Jellyfin Smart Playlists", Device="Server", DeviceId="jellyfin-smart-playlists", Version="1.0.0", Token="{key}"''',
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as c:
        def show(label, r):
            print(f"\n{'='*60}\n{label}\nStatus: {r.status_code}\nBody:   {r.text[:4000]}\n")

        # Get a real playlist ID if not supplied — list all playlists for first user
        if not pl_id:
            r = await c.get(f"{base}/Users", headers=hdrs)
            show("GET /Users", r)
            if r.status_code == 200 and r.json():
                uid = r.json()[0]["Id"]
                rp = await c.get(f"{base}/Users/{uid}/Items", headers=hdrs, params={"IncludeItemTypes": "Playlist", "Recursive": "true", "Limit": "1"})
                show(f"GET playlists for user {uid}", rp)
                if rp.status_code == 200 and rp.json().get("Items"):
                    pl_id = rp.json()["Items"][0]["Id"]
                    print(f"Using playlist: {pl_id}")

        if not pl_id:
            print("No playlist ID available to test."); return

        show("STEP 0: GET /Playlists/{id}", await c.get(f"{base}/Playlists/{pl_id}", headers=hdrs))
        show('STEP 1: POST /Playlists/{id} {"IsPublic": false}',
             await c.post(f"{base}/Playlists/{pl_id}", headers=hdrs, json={"IsPublic": False}))
        show('STEP 2: POST /Playlists/{id} {"IsPublic": false, "Name": "test", "Ids": [], "Users": []}',
             await c.post(f"{base}/Playlists/{pl_id}", headers=hdrs, json={"IsPublic": False, "Name": "test", "Ids": [], "Users": []}))
        show('STEP 3: POST /Playlists/{id}?userId=... {"IsPublic": false}  (using first user)',
             await c.post(f"{base}/Playlists/{pl_id}", headers=hdrs, json={"IsPublic": False}))

asyncio.run(main())
