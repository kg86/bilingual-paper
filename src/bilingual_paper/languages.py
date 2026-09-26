from __future__ import annotations

DEFAULT_LANG = "ja"

# BCP-47-ish code -> English display name, used both in the translation prompt
# ("into natural {name}") and as the HTML lang="" attribute on rendered output.
LANGUAGES: dict[str, str] = {
    "ja": "Japanese",
    "ko": "Korean",
    "zh-Hans": "Simplified Chinese",
    "zh-Hant": "Traditional Chinese",
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
}


def language_name(code: str) -> str:
    try:
        return LANGUAGES[code]
    except KeyError:
        raise ValueError(
            f"Unknown target language code {code!r}. Known codes: {', '.join(sorted(LANGUAGES))}."
        ) from None


RTL_LANGUAGES = frozenset({"ar"})


def text_direction(code: str) -> str:
    """HTML dir="" value for translated text in this language."""
    return "rtl" if code in RTL_LANGUAGES else "ltr"
