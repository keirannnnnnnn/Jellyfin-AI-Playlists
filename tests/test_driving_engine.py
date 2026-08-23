import pytest
from unittest.mock import AsyncMock
from app.services.driving_engine import get_driving_mix_candidates
from app.services.gemini_client import GeminiClient


@pytest.mark.asyncio
async def test_driving_mix_tier1_bpm_resolution():
    # If BPM is populated and >= min_count, Tier 1 is selected
    tracks = [
        {"item_id": f"bpm_{i}", "title": f"BPM Track {i}", "bpm": 128.0, "genres": ["Electronic"]}
        for i in range(15)
    ]
    config = {
        "min_bpm": 115,
        "max_bpm": 145,
        "min_track_count": 10,
        "target_track_count": 40,
        "energy_allow_genres": ["rock"],
        "energy_deny_genres": ["ambient"],
    }

    candidates, method = await get_driving_mix_candidates(tracks, config)
    assert method == "bpm_metadata"
    assert len(candidates) == 15


@pytest.mark.asyncio
async def test_driving_mix_tier2_genre_heuristic():
    # BPM not populated, but energy allowlist yields enough tracks
    tracks = [
        {"item_id": f"rock_{i}", "title": f"Rock Track {i}", "bpm": None, "genres": ["Hard Rock"]}
        for i in range(45)
    ] + [
        {"item_id": "ambient_1", "title": "Calm Track", "bpm": None, "genres": ["Ambient Meditation"]}
    ]

    config = {
        "min_bpm": 115,
        "max_bpm": 145,
        "min_track_count": 10,
        "target_track_count": 40,
        "energy_allow_genres": ["rock", "hard rock"],
        "energy_deny_genres": ["ambient", "meditation"],
    }

    candidates, method = await get_driving_mix_candidates(tracks, config)
    assert method == "genre_energy_heuristic"
    assert len(candidates) == 45
    assert all("ambient" not in t["genres"][0].lower() for t in candidates)


@pytest.mark.asyncio
async def test_driving_mix_tier3_gemini_fallback():
    # Not enough BPM or genre tracks -> calls Gemini
    tracks = [
        {"item_id": f"indie_{i}", "title": f"Indie Song {i}", "bpm": None, "genres": ["Indie"]}
        for i in range(12)
    ]

    config = {
        "min_bpm": 115,
        "max_bpm": 145,
        "min_track_count": 10,
        "target_track_count": 40,
        "energy_allow_genres": ["synthwave"],
        "energy_deny_genres": ["ambient"],
        "use_gemini_fallback": True,
    }

    mock_gemini = GeminiClient(api_key="mock_key")
    mock_gemini.evaluate_driving_tracks = AsyncMock(return_value=["indie_0", "indie_1", "indie_2"])

    candidates, method = await get_driving_mix_candidates(tracks, config, gemini_client=mock_gemini)
    assert method == "gemini_ai_augmented"
    assert len(candidates) == 3
    mock_gemini.evaluate_driving_tracks.assert_called_once()
