# Jellyfin Smart Playlist Generator 🎵

A self-hosted service that connects to a Jellyfin server, reads per-user listening history (via the **Playback Reporting** plugin), and automatically generates a fixed set of dynamic "smart" playlists for each user — written directly into their own Jellyfin account as private playlists with custom mix artwork.

Includes a password-gated Web UI for configuration, on-demand manual runs, detailed run logs, candidate pool statistics, and custom icon management.

Runs as a single lightweight container or standalone Python service, reachable exclusively over **Tailscale** on port **8067**.

---

## 🌟 Key Features

1. **6 Smart Mixes Generated Per User**:
   - **Pop Mix** — Genre & alias matching (Pop, Synthpop, Dance-Pop, Electropop, etc.)
   - **Hip Hop Mix** — Genre & alias matching (Hip Hop, Rap, Trap, Boom Bap, etc.)
   - **2000s Mix** — Tracks released between 2000 and 2009
   - **2010s Mix** — Tracks released between 2010 and 2019
   - **2020s Mix** — Tracks released between 2020 and 2029
   - **Driving Mix** — 3-Tier priority resolution:
     1. *Tier 1*: BPM/tempo metadata filtering (115–145 BPM) if populated in library.
     2. *Tier 2*: Genre-based energy heuristics (allowlist of upbeat/driving genres, denylist of calm/ambient genres).
     3. *Tier 3 (AI Fallback)*: Google Gemini evaluates candidate track metadata for driving energy.

2. **Per-User Intelligent Weighting**:
   - Skews playlist selections heavily toward the user's top favorites and recent listening history using exponential recency decay ($e^{-\Delta t / 30\text{ days}}$) combined with Efraimidis-Spirakis weighted sampling without replacement.

3. **Smart Skip Logic (Zero Wasted Runs)**:
   - Tracks each user's last generation snapshot against Playback Reporting event timestamps.
   - Automatically skips users with no new listening activity (`skipped_no_activity`), leaving existing playlists untouched and saving API calls.
   - Generates initial baseline playlists for new users automatically on first run.

4. **Update-in-Place & Custom Icon Push**:
   - Never creates duplicate playlists on repeated runs; updates playlist items in place.
   - Upload global icons per mix type and push them directly to all users' Jellyfin playlists via Jellyfin's Primary Image API.

5. **Integrated Web UI (Port 8067)**:
   - **Dashboard**: Run status, next scheduled run, per-user activity cards, quick trigger.
   - **Playlists & Icons**: Mix configuration editor, custom icon upload, "Push Icons" button.
   - **Manual Trigger**: Scoped run (All/Single User, All/Single Mix) + Force run toggle.
   - **Run Logs**: Expandable per-user and per-mix breakdowns with exact statuses and error notes.
   - **Stats & Candidate Pools**: Surface thin pools across the library before runs.
   - **Settings & Diagnostics**: Jellyfin connection tester, Gemini API tester with live error diagnostics and fixing guidance, scheduler cron settings, password updater.

6. **Tailscale-Only Isolation**:
   - Binds directly to the host's Tailscale interface/IP (`100.x.y.z`).
   - Zero public exposure; strictly avoids Tailscale Funnel.

---

## 🚀 Quick Start & Deployment

### Method 1: Docker Compose (Recommended)

1. Clone or copy the repository onto your host machine:
   ```bash
   git clone <repo-url> "Jellyfin AI Playlists"
   cd "Jellyfin AI Playlists"
   ```

2. Start the service using Docker Compose:
   ```bash
   docker compose up -d
   ```

3. Open the Web UI over your Tailscale network:
   ```
   http://<YOUR_TAILSCALE_IP>:8067
   ```
   - **Default Admin Password**: `Password123` (change immediately in **Settings**).

> [!NOTE]
> **Connecting to Native Windows Jellyfin Server**:
> If Jellyfin runs as the native Windows desktop app (not in Docker), the default `docker-compose.yml` is already preconfigured with `extra_hosts: ["host.docker.internal:host-gateway"]` and `JELLYFIN_URL=http://host.docker.internal:8096`.

---

### Method 2: Standalone Python Service (Native Windows / Linux)

1. Ensure **Python 3.12+** is installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Launch the service:
   ```bash
   python -m app.main
   ```
4. Access at `http://127.0.0.1:8067` or `http://<YOUR_TAILSCALE_IP>:8067`.

---

## 🔧 Jellyfin & Plugin Setup

### 1. Jellyfin API Key
1. In your Jellyfin Server Dashboard, navigate to **Administration** &rarr; **API Keys**.
2. Click **+** to generate an API key (e.g. name it `Smart Playlists`).
3. Copy the key into the service's **Settings** page in the Web UI.

### 2. Playback Reporting Plugin
The service queries listening history through the official Jellyfin **Playback Reporting** plugin:
1. In Jellyfin Dashboard, go to **Plugins** &rarr; **Catalog**.
2. Install **Playback Reporting** and restart the Jellyfin server.
3. In the Smart Playlist Web UI under **Settings**, click **Test Connection** to verify that `Playback Reporting Plugin: Active` is reported.
4. *Fallback*: If the plugin API is unreachable, the service can also directly read a mounted `playback_reporting.db` SQLite file or gracefully fall back to Jellyfin's native `UserData` play history.

---

## ✨ Google Gemini AI Setup & Error Diagnostics

Gemini is used exclusively as a **Tier-3 fallback** for the Driving Mix when tracks lack BPM tags and genre heuristics don't reach the target track count.

1. Obtain a free API key from [Google AI Studio](https://aistudio.google.com/).
2. In the Web UI, navigate to **Settings** &rarr; **Google Gemini AI Configuration**.
3. Enter your API key and select your preferred model (e.g. `gemini-1.5-flash`).
4. Click **Test Gemini API** to confirm status.

### 🚨 In-UI Error Visibility
If Gemini encounters an API error (such as an invalid API key, model quota limits, or network timeouts):
- A high-visibility **warning banner** appears on the Web UI Dashboard and Base Layout.
- Detailed error messages and resolution suggestions are displayed on the **Settings** page.
- The exact failure is recorded in the **Run Logs** for that mix entry.

---

## 🔒 Tailscale-Only Network Isolation

This service is engineered to be reachable exclusively within your Tailscale Tailnet:

1. **Direct IP Binding**: By default, the application detects the Tailscale CGNAT IP address (`100.64.0.0/10`) on the host and binds the server directly to it.
2. **Environment Variable Override**: You can explicitly specify the bind host via `BIND_HOST`:
   - `BIND_HOST=tailscale0`
   - `BIND_HOST=100.115.92.45`
3. **Tailscale Funnel**: Tailscale Funnel is **disabled and never used** in this project to prevent public internet access.
4. **Verifying Tailscale Binding**:
   - Run `netstat -ano | findstr 8067` (Windows) or `ss -tulpn | grep 8067` (Linux) to verify the socket is bound to the Tailscale `100.x.y.z` address rather than `0.0.0.0`.

---

## ⏰ Daily Scheduler

- Automated refreshed runs are scheduled via in-process **APScheduler** (default: **2:00 AM daily**).
- Both the scheduled job and the manual **"Run Generator Now"** button execute the identical underlying generation pipeline.
- The scheduled hour, minute, and active state can be modified at any time in **Settings** without restarting the service.

---

## 🧪 Running Automated Tests

Run the full automated test suite with pytest:

```bash
python -m pytest -v
```

Test coverage includes:
- Authentication & HMAC session token validation
- SQLite migrations, settings CRUD, and mix definitions
- Filter engine (genre aliases, decade bounds)
- Weighted candidate scoring and Efraimidis-Spirakis sampling
- Driving mix 3-tier priority logic and Gemini fallback
- Jellyfin API communication, playlist creation, and image push
- Generator orchestrator skip logic & baseline generation
- Web UI routes & JSON API endpoints
