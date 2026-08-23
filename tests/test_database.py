import pytest
from pathlib import Path
from app.database import (
    init_db,
    get_setting,
    set_setting,
    get_all_settings,
    get_mix_definitions,
    save_mix_definition,
    get_mix_definition,
    set_gemini_status,
    get_gemini_status,
)


def test_database_initialization_and_seeds(tmp_path: Path):
    db_file = tmp_path / "test.db"
    init_db(db_file)

    # Verify settings seeded
    assert get_setting("jellyfin_url", db_file=db_file) == "http://127.0.0.1:8096"
    assert get_setting("schedule_hour", db_file=db_file) == "2"

    # Verify 6 default mixes seeded
    mixes = get_mix_definitions(db_file=db_file)
    assert len(mixes) == 6
    mix_keys = {m["mix_key"] for m in mixes}
    assert mix_keys == {"pop", "hiphop", "2000s", "2010s", "2020s", "driving"}


def test_settings_get_set(tmp_path: Path):
    db_file = tmp_path / "test.db"
    init_db(db_file)

    set_setting("custom_key", "custom_val", db_file=db_file)
    assert get_setting("custom_key", db_file=db_file) == "custom_val"

    all_s = get_all_settings(db_file=db_file)
    assert all_s["custom_key"] == "custom_val"


def test_mix_definitions_crud(tmp_path: Path):
    db_file = tmp_path / "test.db"
    init_db(db_file)

    save_mix_definition(
        mix_key="rock",
        display_name="Rock Mix",
        mix_type="genre",
        config={"genres": ["rock", "hard rock"], "target_track_count": 50},
        icon_path="static/icons/rock.svg",
        db_file=db_file,
    )

    mix = get_mix_definition("rock", db_file=db_file)
    assert mix is not None
    assert mix["display_name"] == "Rock Mix"
    assert mix["config"]["target_track_count"] == 50


def test_gemini_status_tracking(tmp_path: Path):
    db_file = tmp_path / "test.db"
    init_db(db_file)

    set_gemini_status("error", "API Key Invalid (HTTP 400)", "gemini-1.5-flash", db_file=db_file)
    st = get_gemini_status(db_file=db_file)
    assert st["status"] == "error"
    assert "API Key Invalid" in st["last_error"]

    set_gemini_status("ok", None, "gemini-1.5-flash", db_file=db_file)
    st2 = get_gemini_status(db_file=db_file)
    assert st2["status"] == "ok"
    assert st2["last_error"] is None
