"""Versioned formatting profiles stored as JSON next to this file."""
import json
from pathlib import Path

_DIR = Path(__file__).parent


class UnknownProfile(Exception):
    pass


def list_profiles() -> list:
    out = []
    for f in sorted(_DIR.glob("*.json")):
        out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def load_profile(profile_id: str) -> dict:
    for p in list_profiles():
        if p["id"] == profile_id:
            return p
    raise UnknownProfile(profile_id)
