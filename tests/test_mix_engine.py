import pytest
from datetime import datetime, timedelta
from app.services.mix_engine import (
    normalize_genre,
    genre_matches,
    filter_tracks_by_mix,
    compute_track_weights,
    select_weighted_tracks,
)


def test_genre_matching_aliases():
    assert genre_matches(["Hip Hop"], ["hip hop", "rap"]) is True
    assert genre_matches(["Hip-Hop"], ["hip hop", "rap"]) is True
    assert genre_matches(["Conscious Rap"], ["rap"]) is True
    assert genre_matches(["Synthpop"], ["pop"]) is True
    assert genre_matches(["Death Metal"], ["ambient"]) is False
    assert genre_matches([], ["pop"]) is False


def test_filter_tracks_by_genre_and_decade():
    tracks = [
        {"item_id": "1", "title": "Track 1", "genres": ["Pop"], "production_year": 2005},
        {"item_id": "2", "title": "Track 2", "genres": ["Hip-Hop"], "production_year": 2015},
        {"item_id": "3", "title": "Track 3", "genres": ["Rock"], "production_year": 2008},
        {"item_id": "4", "title": "Track 4", "genres": ["Dance Pop"], "production_year": 2022},
    ]

    # Test Genre Filter
    pop_candidates = filter_tracks_by_mix(tracks, "genre", {"genres": ["pop", "dance pop"]})
    assert len(pop_candidates) == 2
    assert {t["item_id"] for t in pop_candidates} == {"1", "4"}

    # Test Decade Filter (2000s: 2000-2009)
    decade_candidates = filter_tracks_by_mix(tracks, "decade", {"min_year": 2000, "max_year": 2009})
    assert len(decade_candidates) == 2
    assert {t["item_id"] for t in decade_candidates} == {"1", "3"}


def test_track_weighting_and_selection():
    now = datetime.now()
    candidates = [
        {"item_id": f"t_{i}", "title": f"Song {i}"} for i in range(20)
    ]

    # User activity: t_0 played 50 times yesterday, t_1 played 2 times 100 days ago, others unplayed
    user_map = {
        "t_0": {"play_count": 50, "last_played": (now - timedelta(days=1)).isoformat()},
        "t_1": {"play_count": 2, "last_played": (now - timedelta(days=100)).isoformat()},
    }

    weighted = compute_track_weights(candidates, user_map, now=now)
    # Find t_0 weight
    t0_weight = next(w for t, w in weighted if t["item_id"] == "t_0")
    t1_weight = next(w for t, w in weighted if t["item_id"] == "t_1")
    t_unplayed = next(w for t, w in weighted if t["item_id"] == "t_5")

    assert t0_weight > t1_weight > t_unplayed
    assert t_unplayed == 1.0

    # Test weighted selection
    selected_ids, status = select_weighted_tracks(weighted, target_count=10, min_count=5)
    assert status == "generated"
    assert len(selected_ids) == 10
    # Heavy favorite t_0 should almost always be included in the top 10
    assert "t_0" in selected_ids


def test_thin_pool_handling():
    candidates = [
        {"item_id": "1", "title": "Only One Song"}
    ]
    weighted = compute_track_weights(candidates, {})
    selected_ids, status = select_weighted_tracks(weighted, target_count=40, min_count=10)

    assert status == "skipped_thin_pool"
    assert len(selected_ids) == 0
