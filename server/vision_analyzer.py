"""
Vision analyzer module.

Provides a swappable interface for analyzing screenshot images.
Current implementation uses Google Gemini API.

Interface: analyze(image_bytes: bytes) -> dict
Returns: {"label": str, "description": str, "category": str, "confidence": float}
"""

import json
import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Analyze this screenshot of a user's browser tab. 
Describe what the user appears to be doing in a single concise activity label.

Return ONLY valid JSON with exactly these keys, no markdown fences, no extra text:
{"label": "<short activity label>", "description": "<1-2 sentence description of what the user is doing>", "category": "<one of: browsing, coding, communication, productivity, entertainment, shopping, research, social_media, other>", "confidence": <float 0.0-1.0>}"""


class VisionAnalyzer(Protocol):
    """Protocol for vision analyzers, making the provider swappable."""
    async def analyze(self, image_bytes: bytes) -> dict: ...


class GeminiVisionAnalyzer:
    """Analyzes screenshots using Google Gemini API."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model_name = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def analyze(self, image_bytes: bytes) -> dict:
        """
        Analyze a screenshot image and return structured activity data.

        Args:
            image_bytes: Raw PNG image bytes.

        Returns:
            dict with keys: label, description, category, confidence
        """
        from google.genai import types
        client = self._get_client()

        response = client.models.generate_content(
            model=self.model_name,
            contents=[
                types.Content(
                    parts=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                        types.Part.from_text(text=ANALYSIS_PROMPT),
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=256,
            ),
        )

        raw_text = response.text.strip()
        # Strip markdown fences if the model wraps them anyway
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        result = json.loads(raw_text)

        # Validate expected keys
        expected_keys = {"label", "description", "category", "confidence"}
        if not expected_keys.issubset(result.keys()):
            missing = expected_keys - result.keys()
            raise ValueError(f"Gemini response missing keys: {missing}")

        # Clamp confidence
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

        return result


def get_analyzer() -> GeminiVisionAnalyzer:
    """Factory function. Replace this to swap providers."""
    return GeminiVisionAnalyzer()
