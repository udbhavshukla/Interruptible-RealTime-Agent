"""Deterministic mock STT: WAV bytes -> transcript + confidence.

No external API. Decodes UTF-8 payload text when possible (test harness can
embed "SAY:<text>" in fake WAV bytes); otherwise returns a stable placeholder
derived from a SHA-256 hash so output is deterministic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float
    source: str = "audio"


def transcribe_wav(wav_bytes: bytes) -> Transcript:
    if not wav_bytes:
        return Transcript(text="", confidence=0.0)
    try:
        raw = bytes(wav_bytes).decode("utf-8", errors="strict")
    except Exception:
        raw = ""
    if raw.startswith("SAY:"):
        text = raw[4:].strip()
        return Transcript(text=text, confidence=0.93)
    if raw.strip():
        # Plain-text fallback (still deterministic, slightly lower conf).
        return Transcript(text=raw.strip(), confidence=0.81)
    digest = hashlib.sha256(bytes(wav_bytes)).hexdigest()
    return Transcript(text=f"audio_{digest[:8]}", confidence=0.55)
