"""Product language policy for VoxMaestro TTS.

POCKET_TTS_LANGUAGES is what Pocket TTS can do. ENABLED_LANGUAGES is what
this runtime will advertise and accept. Current slice: English and Spanish.
"""

from __future__ import annotations

from voxmaestro.tts.contract import POCKET_TTS_LANGUAGES, LanguageSupport

ENABLED_LANGUAGES: frozenset[str] = frozenset({"en", "es"})


def enabled_language_support() -> tuple[LanguageSupport, ...]:
    """Return Pocket language rows that VoxMaestro currently ships."""
    return tuple(item for item in POCKET_TTS_LANGUAGES if item.code in ENABLED_LANGUAGES)


def require_enabled(code: str) -> str:
    """Return ``code`` or raise if it is outside the shipping slice."""
    if code not in ENABLED_LANGUAGES:
        raise ValueError(f"language {code!r} is not enabled; shipping en/es only")
    return code
