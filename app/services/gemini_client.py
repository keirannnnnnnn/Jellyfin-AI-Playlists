import json
import logging
import httpx
from datetime import datetime
from app.database import set_gemini_status, get_gemini_status

logger = logging.getLogger("jellyfin_playlists.gemini")

GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash", timeout: float = 30.0):
        self.api_key = api_key.strip()
        self.model = model.strip() if model else "gemini-1.5-flash"
        self.timeout = timeout

    async def test_connection(self) -> dict:
        """Test Gemini API key and model availability, saving status to DB."""
        result = {
            "success": False,
            "status": "error",
            "model": self.model,
            "error": None,
            "checked_at": datetime.now().isoformat(),
        }

        if not self.api_key:
            err = "Gemini API key is empty or not configured."
            result["error"] = err
            set_gemini_status("not_configured", err, self.model)
            return result

        url = f"{GEMINI_API_ENDPOINT.format(model=self.model)}?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": "Ping test. Respond with the single word: OK"}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 10,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        result["success"] = True
                        result["status"] = "ok"
                        result["error"] = None
                        set_gemini_status("ok", None, self.model)
                        return result
                    else:
                        err = f"Gemini returned empty candidates: {resp.text}"
                        result["error"] = err
                        set_gemini_status("error", err, self.model)
                        return result
                else:
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("error", {}).get("message", resp.text)
                        status_code = err_json.get("error", {}).get("status", str(resp.status_code))
                        err = f"Gemini API Error ({status_code}): {err_msg}"
                    except Exception:
                        err = f"Gemini API Error (HTTP {resp.status_code}): {resp.text}"

                    result["error"] = err
                    set_gemini_status("error", err, self.model)
                    return result
        except httpx.ConnectError as e:
            err = f"Network connection to Gemini API failed: {str(e)}"
            result["error"] = err
            set_gemini_status("error", err, self.model)
            return result
        except Exception as e:
            err = f"Unexpected Gemini error: {str(e)}"
            result["error"] = err
            set_gemini_status("error", err, self.model)
            return result

    async def evaluate_driving_tracks(self, candidate_tracks: list[dict], batch_size: int = 50) -> list[str]:
        """Ask Gemini to identify driving/high-energy songs from title, artist, genres metadata.

        Returns list of item_ids classified as driving tracks.
        """
        if not self.api_key:
            err = "Gemini API key not configured, skipping AI evaluation."
            logger.warning(err)
            set_gemini_status("not_configured", err, self.model)
            return []

        approved_ids: list[str] = []
        url = f"{GEMINI_API_ENDPOINT.format(model=self.model)}?key={self.api_key}"

        for i in range(0, len(candidate_tracks), batch_size):
            batch = candidate_tracks[i:i + batch_size]
            prompt_tracks = [
                {
                    "id": t["item_id"],
                    "title": t.get("title", "Unknown"),
                    "artist": t.get("artist", "Unknown"),
                    "genres": t.get("genres", []),
                    "year": t.get("production_year"),
                }
                for t in batch
            ]

            prompt = (
                "You are an expert music curator creating a 'Driving Mix' (energetic, rhythmically engaging, "
                "road-trip suitable, high-tempo or driving beat, upbeat, engaging - rock, dance, electronic, "
                "driving pop, upbeat hip-hop, synthwave, etc. NOT slow ballads, ambient, quiet acoustic, lullabies, or sleep music).\n\n"
                "Evaluate the following tracks and return a JSON array containing ONLY the IDs of tracks that fit a Driving Mix.\n"
                "Input tracks JSON:\n"
                f"{json.dumps(prompt_tracks, indent=2)}\n\n"
                "Return output strictly in JSON format matching this schema:\n"
                '{"driving_track_ids": ["id1", "id2", ...]}'
            )

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                },
            }

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code != 200:
                        try:
                            err_json = resp.json()
                            err_msg = err_json.get("error", {}).get("message", resp.text)
                            err_status = err_json.get("error", {}).get("status", str(resp.status_code))
                            full_err = f"Gemini API Error ({err_status}): {err_msg}"
                        except Exception:
                            full_err = f"Gemini API Error (HTTP {resp.status_code}): {resp.text}"

                        logger.error(full_err)
                        set_gemini_status("error", full_err, self.model)
                        continue

                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        content_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
                        parsed = json.loads(content_text)
                        batch_approved = parsed.get("driving_track_ids", [])
                        approved_ids.extend([bid for bid in batch_approved if isinstance(bid, str)])
                        set_gemini_status("ok", None, self.model)
                    else:
                        err = "Gemini returned empty candidate content during track evaluation."
                        logger.warning(err)
                        set_gemini_status("error", err, self.model)

            except Exception as e:
                err = f"Gemini track evaluation exception: {str(e)}"
                logger.error(err)
                set_gemini_status("error", err, self.model)

        return approved_ids
