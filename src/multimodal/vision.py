"""Deterministic mock Vision/OCR: PNG bytes -> candidate facts + confidence.

Perception proposes CANDIDATES only -- never committed truth. Test harness
can embed "OCR:<k1=v1;k2=v2>" or "FACTS:device_model=ABC-123,error_code=E42"
as PNG bytes. Otherwise a stable hash-derived placeholder is returned with
low confidence (forcing verification/clarification downstream).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class VisionResult:
    facts: Dict[str, str]
    confidence: float
    source: str = "image"


def _parse_kv(payload: str) -> Dict[str, str]:
    facts: Dict[str, str] = {}
    body = payload.split(":", 1)[1] if ":" in payload else payload
    for chunk in body.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            facts[k] = v
    return facts


def extract_facts(png_bytes: bytes) -> VisionResult:
    if not png_bytes:
        return VisionResult(facts={}, confidence=0.0)
    try:
        raw = bytes(png_bytes).decode("utf-8", errors="strict")
    except Exception:
        raw = ""
    if raw.startswith("OCR:") or raw.startswith("FACTS:"):
        return VisionResult(facts=_parse_kv(raw), confidence=0.88)
    if raw.startswith("LOWCONF:"):
        return VisionResult(facts=_parse_kv("FACTS:" + raw[len("LOWCONF:"):]),
                            confidence=0.61)
    if raw.strip() and "=" in raw:
        return VisionResult(facts=_parse_kv("FACTS:" + raw), confidence=0.70)
    digest = hashlib.sha256(bytes(png_bytes)).hexdigest()
    return VisionResult(facts={"image_hash": digest[:12]}, confidence=0.40)
