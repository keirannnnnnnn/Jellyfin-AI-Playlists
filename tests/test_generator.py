import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from app.database import init_db, get_db
from app.services.generator_service import run_smart_playlists


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracks(prefix="pop", genre="Pop", count=20):
    return [
        {
            "item_id": f"{prefix}_{i}",
            "title": f"{prefix.title()} {i}",
            "artist": "Artist",
            "album": "Alb",
            "genres": [genre],
            "production_year": 2021,
            "duration_ticks": 1000,
            "bpm": None,
        }
        for i in range(count)
    ]


def _patch_jf(**overrides):
    """Return a dict of default mocks for JellyfinClient methods."""
    defaults = {
        "app.services.generator_service.JellyfinClient.get_all_audio_items": AsyncMock(return_value=_make_tracks()),
        "app.services.generator_service.JellyfinClient.get_users": AsyncMock(
            return_value=[{"id": "user_1", "name": "Alice", "is_disabled": False}]
        ),
        "app.services.generator_service.JellyfinClient.get_playback_activity": AsyncMock(return_value=[]),
        "app.services.generator_service.JellyfinClient.playlist_exists": AsyncMock(return_value=True),
        "app.services.generator_service.JellyfinClient.create_playlist": AsyncMock(return_value="new_pl_1"),
        "app.services.generator_service.JellyfinClient.update_playlist_items": AsyncMock(return_value=None),
        "app.services.generator_service.JellyfinClient.set_playlist_image": AsyncMock(return_value=True),
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generator_first_run_baseline_generation(tmp_path: Path):
    """On first run (no prior DB state) the generator creates a fresh playlist."""
    db_file = tmp_path / "gen_test.db"
    init_db(db_file)

    # Patch individually for clarity
    with patch("app.services.generator_service.JellyfinClient.get_all_audio_items", new_callable=AsyncMock) as mock_get_tracks, \
         patch("app.services.generator_service.JellyfinClient.get_users", new_callable=AsyncMock) as mock_get_users, \
         patch("app.services.generator_service.JellyfinClient.get_playback_activity", new_callable=AsyncMock) as mock_get_activity, \
         patch("app.services.generator_service.JellyfinClient.playlist_exists", new_callable=AsyncMock) as mock_exists, \
         patch("app.services.generator_service.JellyfinClient.create_playlist", new_callable=AsyncMock) as mock_create_pl, \
         patch("app.services.generator_service.JellyfinClient.update_playlist_items", new_callable=AsyncMock) as mock_update_pl, \
         patch("app.services.generator_service.JellyfinClient.set_playlist_image", new_callable=AsyncMock) as mock_set_img:

        mock_get_tracks.return_value = _make_tracks()
        mock_get_users.return_value = [{"id": "user_1", "name": "Alice", "is_disabled": False}]
        mock_get_activity.return_value = []
        mock_exists.return_value = True
        mock_create_pl.return_value = "new_pl_1"

        res = await run_smart_playlists(
            trigger="manual",
            target_user_id="user_1",
            target_mix_key="pop",
            force=False,
            db_file=db_file,
        )

        assert res["status"] == "completed"
        assert res["summary"]["generated"] == 1
        assert res["summary"]["skipped_no_activity"] == 0
        # First run: no prior DB state, so create_playlist must be called (not update)
        mock_create_pl.assert_called_once()
        mock_update_pl.assert_not_called()
        # playlist_exists must NOT be called because there was no tracked ID to check
        mock_exists.assert_not_called()

        # Verify DB log entry
        with get_db(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, mix_key, track_count FROM run_log_entries WHERE user_id = 'user_1';")
            entry = cursor.fetchone()
            assert entry["status"] == "generated"
            assert entry["mix_key"] == "pop"
            assert entry["track_count"] == 20

        # Verify the new playlist ID was stored in user_playlist_state
        with get_db(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT jellyfin_playlist_id FROM user_playlist_state WHERE user_id='user_1' AND mix_key='pop';")
            row = cursor.fetchone()
            assert row is not None
            assert row["jellyfin_playlist_id"] == "new_pl_1"


@pytest.mark.asyncio
async def test_generator_skip_logic_when_no_new_activity(tmp_path: Path):
    """Subsequent run with no new activity is skipped; force=True overrides the skip."""
    db_file = tmp_path / "gen_skip_test.db"
    init_db(db_file)

    mock_users = [{"id": "user_2", "name": "Bob", "is_disabled": False}]

    with patch("app.services.generator_service.JellyfinClient.get_all_audio_items", new_callable=AsyncMock) as mock_get_tracks, \
         patch("app.services.generator_service.JellyfinClient.get_users", new_callable=AsyncMock) as mock_get_users, \
         patch("app.services.generator_service.JellyfinClient.get_playback_activity", new_callable=AsyncMock) as mock_get_activity, \
         patch("app.services.generator_service.JellyfinClient.playlist_exists", new_callable=AsyncMock) as mock_exists, \
         patch("app.services.generator_service.JellyfinClient.create_playlist", new_callable=AsyncMock) as mock_create_pl, \
         patch("app.services.generator_service.JellyfinClient.update_playlist_items", new_callable=AsyncMock) as mock_update_pl, \
         patch("app.services.generator_service.JellyfinClient.set_playlist_image", new_callable=AsyncMock):

        mock_get_tracks.return_value = _make_tracks()
        mock_get_users.return_value = mock_users
        mock_get_activity.return_value = []
        mock_exists.return_value = True  # tracked playlist still exists
        mock_create_pl.return_value = "new_pl_2"

        # 1st run: Baseline generation (first run, no DB state → create)
        res1 = await run_smart_playlists(target_user_id="user_2", target_mix_key="pop", db_file=db_file)
        assert res1["summary"]["generated"] == 1
        mock_create_pl.assert_called_once()

        # 2nd run: No new playback events → skip
        mock_create_pl.reset_mock()
        mock_update_pl.reset_mock()
        res2 = await run_smart_playlists(target_user_id="user_2", target_mix_key="pop", force=False, db_file=db_file)
        assert res2["summary"]["generated"] == 0
        assert res2["summary"]["skipped_no_activity"] == 1
        mock_create_pl.assert_not_called()
        mock_update_pl.assert_not_called()

        # 3rd run: force=True → generates even with no new events
        # 2nd run had a tracked playlist in DB now (from run 1), so expects update not create
        mock_create_pl.reset_mock()
        mock_update_pl.reset_mock()
        res3 = await run_smart_playlists(target_user_id="user_2", target_mix_key="pop", force=True, db_file=db_file)
        assert res3["summary"]["generated"] == 1
        # Should have updated the existing tracked playlist, not created a new one
        mock_update_pl.assert_called_once()
        mock_create_pl.assert_not_called()


@pytest.mark.asyncio
async def test_generator_recreates_playlist_on_404(tmp_path: Path):
    """If a tracked playlist returns 404 (deleted externally), a new playlist is created
    and the DB is updated with the new ID.  The generator must NOT search by name."""
    db_file = tmp_path / "gen_404_test.db"
    init_db(db_file)

    mock_users = [{"id": "user_3", "name": "Carol", "is_disabled": False}]

    with patch("app.services.generator_service.JellyfinClient.get_all_audio_items", new_callable=AsyncMock) as mock_get_tracks, \
         patch("app.services.generator_service.JellyfinClient.get_users", new_callable=AsyncMock) as mock_get_users, \
         patch("app.services.generator_service.JellyfinClient.get_playback_activity", new_callable=AsyncMock) as mock_get_activity, \
         patch("app.services.generator_service.JellyfinClient.playlist_exists", new_callable=AsyncMock) as mock_exists, \
         patch("app.services.generator_service.JellyfinClient.create_playlist", new_callable=AsyncMock) as mock_create_pl, \
         patch("app.services.generator_service.JellyfinClient.update_playlist_items", new_callable=AsyncMock) as mock_update_pl, \
         patch("app.services.generator_service.JellyfinClient.set_playlist_image", new_callable=AsyncMock):

        mock_get_tracks.return_value = _make_tracks()
        mock_get_users.return_value = mock_users
        mock_get_activity.return_value = []
        mock_exists.return_value = True
        mock_create_pl.return_value = "original_id"

        # Run 1: establish tracked ID "original_id"
        res1 = await run_smart_playlists(target_user_id="user_3", target_mix_key="pop", db_file=db_file)
        assert res1["summary"]["generated"] == 1

        with get_db(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT jellyfin_playlist_id FROM user_playlist_state WHERE user_id='user_3' AND mix_key='pop';")
            assert cursor.fetchone()["jellyfin_playlist_id"] == "original_id"

        # Run 2: simulate user deleted the playlist (playlist_exists → False)
        mock_exists.return_value = False
        mock_create_pl.reset_mock()
        mock_create_pl.return_value = "recreated_id"
        mock_update_pl.reset_mock()

        res2 = await run_smart_playlists(target_user_id="user_3", target_mix_key="pop", force=True, db_file=db_file)
        assert res2["summary"]["generated"] == 1

        # Must have created a brand-new playlist (not tried to update the deleted one)
        mock_create_pl.assert_called_once()
        mock_update_pl.assert_not_called()

        # DB must now store the new ID
        with get_db(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT jellyfin_playlist_id FROM user_playlist_state WHERE user_id='user_3' AND mix_key='pop';")
            assert cursor.fetchone()["jellyfin_playlist_id"] == "recreated_id"


@pytest.mark.asyncio
async def test_generator_overlapping_run_rejected(tmp_path: Path):
    """A second concurrent run is rejected immediately with status='already_running'."""
    import asyncio
    from app.services import generator_service

    db_file = tmp_path / "gen_lock_test.db"
    init_db(db_file)

    # Manually acquire the lock to simulate a run in progress
    async with generator_service._run_lock:
        result = await run_smart_playlists(
            target_user_id="user_x",
            target_mix_key="pop",
            db_file=db_file,
        )

    assert result["status"] == "already_running"
    assert result["run_id"] is None
    assert "already in progress" in result["error"].lower()
