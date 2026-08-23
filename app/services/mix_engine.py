import re
import math
import random
import logging
from datetime import datetime

logger = logging.getLogger("jellyfin_playlists.mix_engine")


def normalize_genre(genre_str: str) -> str:
    """Normalize genre string by lowering, removing punctuation and extra whitespace."""
    if not genre_str:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", genre_str.lower())
    return " ".join(cleaned.split())


def genre_matches(track_genres: list[str], allowed_genres: list[str]) -> bool:
    """Check if any track genre matches any allowed genre (exact or normalized substring/alias)."""
    if not track_genres or not allowed_genres:
        return False

    norm_allowed = [normalize_genre(g) for g in allowed_genres if g]
    norm_track_genres = [normalize_genre(g) for g in track_genres if g]

    for tg in norm_track_genres:
        if not tg:
            continue
        for ag in norm_allowed:
            if not ag:
                continue
            # Direct match or substring word boundary match
            if tg == ag or ag in tg or tg in ag:
                return True
            # Split tokens check (e.g. "hip hop" matches "hip-hop" or "hiphop")
            tg_compact = tg.replace(" ", "")
            ag_compact = ag.replace(" ", "")
            if tg_compact == ag_compact or ag_compact in tg_compact:
                return True

    return False


def filter_tracks_by_mix(tracks: list[dict], mix_type: str, config: dict) -> list[dict]:
    """Shared filter engine for genre and decade mixes."""
    candidates = []

    if mix_type == "genre":
        allowed_genres = config.get("genres", [])
        for track in tracks:
            t_genres = track.get("genres", [])
            if genre_matches(t_genres, allowed_genres):
                candidates.append(track)

    elif mix_type == "decade":
        min_year = config.get("min_year")
        max_year = config.get("max_year")
        for track in tracks:
            year = track.get("production_year")
            if year is not None:
                try:
                    year_int = int(year)
                    if min_year is not None and year_int < min_year:
                        continue
                    if max_year is not None and year_int > max_year:
                        continue
                    candidates.append(track)
                except (ValueError, TypeError):
                    continue

    return candidates


def compute_track_weights(
    candidate_tracks: list[dict],
    user_activity_map: dict[str, dict],
    now: datetime | None = None,
) -> list[tuple[dict, float]]:
    """Compute selection weight for each candidate track based on user play count and recency.

    user_activity_map: dict mapping item_id -> {"play_count": int, "last_played": datetime}
    """
    if now is None:
        now = datetime.now()

    weighted_tracks = []
    for track in candidate_tracks:
        item_id = track["item_id"]
        activity = user_activity_map.get(item_id, {})
        play_count = activity.get("play_count", 0)
        last_played = activity.get("last_played")

        # Base weight for discovery
        weight = 1.0

        # Skew heavily by play count
        if play_count > 0:
            weight += min(play_count * 2.5, 50.0)

        # Recency boost (exponential decay over 30 days)
        if last_played:
            try:
                if isinstance(last_played, str):
                    lp_dt = datetime.fromisoformat(last_played.replace("Z", "+00:00")).replace(tzinfo=None)
                else:
                    lp_dt = last_played.replace(tzinfo=None) if last_played.tzinfo else last_played

                days_ago = max(0.0, (now - lp_dt).total_seconds() / 86400.0)
                recency_factor = math.exp(-days_ago / 30.0)  # 1.0 today, ~0.37 at 30 days, ~0.13 at 60 days
                weight += recency_factor * 5.0
            except Exception as e:
                logger.debug(f"Recency parsing failed for {item_id}: {e}")

        weighted_tracks.append((track, weight))

    return weighted_tracks


def select_weighted_tracks(
    weighted_tracks: list[tuple[dict, float]],
    target_count: int = 40,
    min_count: int = 10,
) -> tuple[list[str], str]:
    """Perform weighted random sampling without replacement (Efraimidis-Spirakis A-Res).

    Returns: (selected_item_ids, status)
    status can be: "generated", "skipped_thin_pool", "empty"
    """
    if not weighted_tracks:
        return [], "skipped_thin_pool"

    total_candidates = len(weighted_tracks)
    if total_candidates < min_count:
        logger.info(f"Thin pool: only {total_candidates} candidates (min {min_count}).")
        return [], "skipped_thin_pool"

    # Efraimidis-Spirakis A-Res sampling
    keys = []
    for track, weight in weighted_tracks:
        u = random.random()
        # Key = u^(1/w)
        key = math.pow(u, 1.0 / max(weight, 0.001))
        keys.append((key, track["item_id"]))

    keys.sort(key=lambda x: x[0], reverse=True)
    selected_ids = [item_id for _, item_id in keys[:target_count]]

    return selected_ids, "generated"
