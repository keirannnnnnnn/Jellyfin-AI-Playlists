import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from app.config import (
    DB_PATH,
    DEFAULT_JELLYFIN_URL,
    DEFAULT_JELLYFIN_API_KEY,
    DEFAULT_JELLYFIN_USERNAME,
    DEFAULT_JELLYFIN_PASSWORD,
    DEFAULT_GEMINI_API_KEY,
    DEFAULT_PLAYBACK_DB_PATH,
    DEFAULT_UI_PASSWORD,
)

logger = logging.getLogger("jellyfin_playlists.database")


def get_db_connection(db_file: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_db(db_file: Path | str = DB_PATH):
    conn = get_db_connection(db_file)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hash_password(password: str, salt: str | None = None) -> str:
    import hashlib
    import secrets

    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${hashed.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    import hashlib
    import hmac

    try:
        salt, hash_val = stored_hash.split("$", 1)
        test_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
        return hmac.compare_digest(hash_val, test_hash)
    except Exception:
        return False


DEFAULT_MIXES = [
    {
        "mix_key": "pop",
        "display_name": "Pop Mix",
        "type": "genre",
        "config": {
            "genres": ["pop", "dance-pop", "synthpop", "electropop", "pop rock", "indie pop", "k-pop", "art pop", "contemporary pop"],
            "target_track_count": 40,
            "min_track_count": 10,
        },
        "icon_path": "static/icons/pop.svg",
    },
    {
        "mix_key": "hiphop",
        "display_name": "Hip Hop Mix",
        "type": "genre",
        "config": {
            "genres": [
                "hip hop",
                "hip-hop",
                "rap",
                "trap",
                "boom bap",
                "conscious hip hop",
                "southern hip hop",
                "gangsta rap",
                "drill",
                "cloud rap",
                "lo-fi hip hop",
                "r&b / hip-hop",
                "urban",
            ],
            "target_track_count": 40,
            "min_track_count": 10,
        },
        "icon_path": "static/icons/hiphop.svg",
    },
    {
        "mix_key": "2000s",
        "display_name": "2000s Mix",
        "type": "decade",
        "config": {
            "min_year": 2000,
            "max_year": 2009,
            "target_track_count": 40,
            "min_track_count": 10,
        },
        "icon_path": "static/icons/2000s.svg",
    },
    {
        "mix_key": "2010s",
        "display_name": "2010s Mix",
        "type": "decade",
        "config": {
            "min_year": 2010,
            "max_year": 2019,
            "target_track_count": 40,
            "min_track_count": 10,
        },
        "icon_path": "static/icons/2010s.svg",
    },
    {
        "mix_key": "2020s",
        "display_name": "2020s Mix",
        "type": "decade",
        "config": {
            "min_year": 2020,
            "max_year": 2029,
            "target_track_count": 40,
            "min_track_count": 10,
        },
        "icon_path": "static/icons/2020s.svg",
    },
    {
        "mix_key": "driving",
        "display_name": "Driving Mix",
        "type": "driving",
        "config": {
            "min_bpm": 115,
            "max_bpm": 145,
            "energy_allow_genres": [
                "rock",
                "electronic",
                "dance",
                "synthwave",
                "retrowave",
                "hip hop",
                "rap",
                "metal",
                "alternative",
                "house",
                "drum and bass",
                "dnb",
                "techno",
                "indie rock",
                "hard rock",
                "edm",
                "trance",
                "pop punk",
                "nu metal",
                "electro",
                "punk",
            ],
            "energy_deny_genres": [
                "ambient",
                "classical",
                "lullaby",
                "acoustic",
                "spoken word",
                "meditation",
                "sleep",
                "podcast",
                "audiobook",
                "ballad",
                "easy listening",
                "relaxation",
            ],
            "use_gemini_fallback": True,
            "target_track_count": 40,
            "min_track_count": 10,
        },
        "icon_path": "static/icons/driving.svg",
    },
]


def init_db(db_file: Path | str = DB_PATH):
    """Create tables and populate default settings/mixes if not present."""
    with get_db(db_file) as conn:
        cursor = conn.cursor()

        # Settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                jellyfin_user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_processed_at TEXT,
                last_event_timestamp TEXT
            );
        """)

        # Songs catalog table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                item_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT,
                album TEXT,
                genres_json TEXT,
                production_year INTEGER,
                duration_ticks INTEGER,
                bpm REAL,
                last_synced_at TEXT NOT NULL
            );
        """)

        # Mix definitions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mix_definitions (
                mix_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                type TEXT NOT NULL,
                config_json TEXT NOT NULL,
                icon_path TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );
        """)

        # User playlist state
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_playlist_state (
                user_id TEXT NOT NULL,
                mix_key TEXT NOT NULL,
                jellyfin_playlist_id TEXT,
                last_generated_at TEXT,
                last_track_count INTEGER DEFAULT 0,
                last_status TEXT,
                PRIMARY KEY (user_id, mix_key)
            );
        """)

        # Run log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_log (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                trigger TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT
            );
        """)

        # Run log entries
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_log_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                mix_key TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                track_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES run_log(run_id) ON DELETE CASCADE
            );
        """)

        # Gemini status / error log table for UI banner & diagnostics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gemini_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_checked_at TEXT,
                status TEXT NOT NULL DEFAULT 'unknown',
                last_error TEXT,
                model TEXT NOT NULL DEFAULT 'gemini-1.5-flash'
            );
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO gemini_status (id, status, last_error, model)
            VALUES (1, 'unknown', NULL, 'gemini-1.5-flash');
        """)

        # Populate default settings if missing
        default_settings = {
            "jellyfin_url": DEFAULT_JELLYFIN_URL,
            "jellyfin_api_key": DEFAULT_JELLYFIN_API_KEY,
            "jellyfin_username": DEFAULT_JELLYFIN_USERNAME,
            "jellyfin_password": DEFAULT_JELLYFIN_PASSWORD,
            "gemini_api_key": DEFAULT_GEMINI_API_KEY,
            "gemini_model": "gemini-1.5-flash",
            "playback_db_path": DEFAULT_PLAYBACK_DB_PATH,
            "schedule_hour": "2",
            "schedule_minute": "0",
            "schedule_enabled": "true",
            "password_hash": hash_password(DEFAULT_UI_PASSWORD),
        }

        for key, val in default_settings.items():
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);",
                (key, val),
            )

        # Seed default mixes if table is empty
        cursor.execute("SELECT COUNT(*) FROM mix_definitions;")
        count = cursor.fetchone()[0]
        if count == 0:
            for mix in DEFAULT_MIXES:
                cursor.execute(
                    """
                    INSERT INTO mix_definitions (mix_key, display_name, type, config_json, icon_path, enabled)
                    VALUES (?, ?, ?, ?, ?, 1);
                    """,
                    (
                        mix["mix_key"],
                        mix["display_name"],
                        mix["type"],
                        json.dumps(mix["config"]),
                        mix["icon_path"],
                    ),
                )
    logger.info("Database initialized successfully.")


# Database helper methods
def get_setting(key: str, default: str = "", db_file: Path | str = DB_PATH) -> str:
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?;", (key,))
        row = cursor.fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str, db_file: Path | str = DB_PATH):
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (key, value),
        )


def get_all_settings(db_file: Path | str = DB_PATH) -> dict[str, str]:
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings;")
        return {row["key"]: row["value"] for row in cursor.fetchall()}


def get_mix_definitions(db_file: Path | str = DB_PATH) -> list[dict]:
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM mix_definitions ORDER BY mix_key;")
        results = []
        for row in cursor.fetchall():
            results.append({
                "mix_key": row["mix_key"],
                "display_name": row["display_name"],
                "type": row["type"],
                "config": json.loads(row["config_json"]),
                "icon_path": row["icon_path"],
                "enabled": bool(row["enabled"]),
            })
        return results


def get_mix_definition(mix_key: str, db_file: Path | str = DB_PATH) -> dict | None:
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM mix_definitions WHERE mix_key = ?;", (mix_key,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "mix_key": row["mix_key"],
            "display_name": row["display_name"],
            "type": row["type"],
            "config": json.loads(row["config_json"]),
            "icon_path": row["icon_path"],
            "enabled": bool(row["enabled"]),
        }


def save_mix_definition(
    mix_key: str,
    display_name: str,
    mix_type: str,
    config: dict,
    icon_path: str | None = None,
    enabled: bool = True,
    db_file: Path | str = DB_PATH,
):
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO mix_definitions (mix_key, display_name, type, config_json, icon_path, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(mix_key) DO UPDATE SET
                display_name = excluded.display_name,
                type = excluded.type,
                config_json = excluded.config_json,
                icon_path = COALESCE(excluded.icon_path, mix_definitions.icon_path),
                enabled = excluded.enabled;
            """,
            (mix_key, display_name, mix_type, json.dumps(config), icon_path, 1 if enabled else 0),
        )


def set_gemini_status(status: str, error: str | None = None, model: str = "gemini-1.5-flash", db_file: Path | str = DB_PATH):
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE gemini_status
            SET last_checked_at = ?, status = ?, last_error = ?, model = ?
            WHERE id = 1;
            """,
            (datetime.now().isoformat(), status, error, model),
        )


def get_gemini_status(db_file: Path | str = DB_PATH) -> dict:
    with get_db(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM gemini_status WHERE id = 1;")
        row = cursor.fetchone()
        if not row:
            return {"status": "unknown", "last_error": None, "last_checked_at": None, "model": "gemini-1.5-flash"}
        return {
            "status": row["status"],
            "last_error": row["last_error"],
            "last_checked_at": row["last_checked_at"],
            "model": row["model"],
        }
