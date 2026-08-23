import logging
from app.services.mix_engine import genre_matches
from app.services.gemini_client import GeminiClient

logger = logging.getLogger("jellyfin_playlists.driving_engine")


async def get_driving_mix_candidates(
    all_tracks: list[dict],
    config: dict,
    gemini_client: GeminiClient | None = None,
) -> tuple[list[dict], str]:
    """Resolve candidate tracks for the Driving Mix via 3-tier priority logic:

    Priority 1: BPM range (if populated on library tracks)
    Priority 2: Genre-based energy heuristic (allowlist & denylist)
    Priority 3: Gemini AI evaluation fallback

    Returns (candidates, resolution_method)
    """
    min_bpm = config.get("min_bpm", 115)
    max_bpm = config.get("max_bpm", 145)
    target_count = config.get("target_track_count", 40)
    min_count = config.get("min_track_count", 10)
    allow_genres = config.get("energy_allow_genres", [])
    deny_genres = config.get("energy_deny_genres", [])
    use_gemini = config.get("use_gemini_fallback", True)

    # -------------------------------------------------------------
    # Tier 1: BPM / Tempo Filtering
    # -------------------------------------------------------------
    bpm_candidates = []
    bpm_populated_count = 0

    for track in all_tracks:
        bpm = track.get("bpm")
        if bpm is not None:
            bpm_populated_count += 1
            if min_bpm <= bpm <= max_bpm:
                # Also ensure not on deny list
                if not genre_matches(track.get("genres", []), deny_genres):
                    bpm_candidates.append(track)

    logger.info(
        f"Driving Mix Tier 1 (BPM): {bpm_populated_count} tracks have BPM populated in library. "
        f"{len(bpm_candidates)} matched range [{min_bpm}-{max_bpm}]."
    )

    if len(bpm_candidates) >= min_count:
        return bpm_candidates, "bpm_metadata"

    # -------------------------------------------------------------
    # Tier 2: Genre-Based Energy Heuristic
    # -------------------------------------------------------------
    genre_candidates = []
    for track in all_tracks:
        t_genres = track.get("genres", [])
        # Exclude deny genres immediately
        if genre_matches(t_genres, deny_genres):
            continue
        # Include if matches allow genres
        if genre_matches(t_genres, allow_genres):
            genre_candidates.append(track)

    logger.info(
        f"Driving Mix Tier 2 (Genre Heuristic): {len(genre_candidates)} tracks matched energy allowlist."
    )

    if len(genre_candidates) >= target_count:
        return genre_candidates, "genre_energy_heuristic"

    # -------------------------------------------------------------
    # Tier 3: Gemini AI Fallback
    # -------------------------------------------------------------
    # Combine tier 2 candidates with remaining non-denied tracks for Gemini evaluation
    if use_gemini and gemini_client and gemini_client.api_key:
        logger.info(
            f"Driving Mix Tier 3 (Gemini AI Fallback): Candidate pool ({len(genre_candidates)}) "
            f"is below target {target_count}. Invoking Gemini for evaluation..."
        )

        existing_candidate_ids = {t["item_id"] for t in genre_candidates}
        potential_tracks = [
            t for t in all_tracks
            if t["item_id"] not in existing_candidate_ids
            and not genre_matches(t.get("genres", []), deny_genres)
        ]

        # Take a slice to evaluate with Gemini (up to 150 items to conserve tokens/time)
        eval_sample = potential_tracks[:150]
        try:
            approved_ids = await gemini_client.evaluate_driving_tracks(eval_sample)
            logger.info(f"Gemini approved {len(approved_ids)} additional driving tracks.")
            approved_id_set = set(approved_ids)

            ai_additional_tracks = [t for t in eval_sample if t["item_id"] in approved_id_set]
            combined = list(genre_candidates) + ai_additional_tracks
            return combined, "gemini_ai_augmented"
        except Exception as e:
            logger.error(f"Gemini fallback evaluation failed: {e}")
            # Fall back to whatever tier 2 had
            return genre_candidates, "genre_energy_heuristic_gemini_failed"

    return genre_candidates, "genre_energy_heuristic"
