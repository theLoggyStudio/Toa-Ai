"""Langues supportées (détection auto + cibles courantes)."""

from typing import Literal

SourceLanguage = Literal[
    "auto",
    "ja",
    "ko",
    "zh",
    "en",
    "es",
    "de",
    "fr",
    "pt",
    "ru",
    "ar",
    "hi",
    "th",
    "vi",
    "id",
]

TargetLanguage = Literal[
    "fr",
    "en",
    "es",
    "de",
    "pt",
    "it",
    "ar",
    "zh",
    "ru",
    "ja",
    "ko",
    "hi",
    "tr",
    "vi",
    "id",
    "pl",
    "nl",
]

SUPPORTED_SOURCE_CODES: tuple[str, ...] = (
    "auto",
    "ja",
    "ko",
    "zh",
    "en",
    "es",
    "de",
    "fr",
    "pt",
    "ru",
    "ar",
    "hi",
    "th",
    "vi",
    "id",
)

SUPPORTED_TARGET_CODES: tuple[str, ...] = (
    "fr",
    "en",
    "es",
    "de",
    "pt",
    "it",
    "ar",
    "zh",
    "ru",
    "ja",
    "ko",
    "hi",
    "tr",
    "vi",
    "id",
    "pl",
    "nl",
)

TARGET_LABELS: dict[str, str] = {
    "fr": "Français",
    "en": "English",
    "es": "Español",
    "de": "Deutsch",
    "pt": "Português",
    "it": "Italiano",
    "ar": "العربية",
    "zh": "中文",
    "ru": "Русский",
    "ja": "日本語",
    "ko": "한국어",
    "hi": "हिन्दी",
    "tr": "Türkçe",
    "vi": "Tiếng Việt",
    "id": "Bahasa Indonesia",
    "pl": "Polski",
    "nl": "Nederlands",
}

SOURCE_LABELS: dict[str, str] = {
    "auto": "Détection automatique",
    **{k: v for k, v in TARGET_LABELS.items() if k in SUPPORTED_SOURCE_CODES},
}

LANG_NAMES_FOR_PROMPT: dict[str, str] = {
    "auto": "détection automatique",
    "ja": "japonais",
    "ko": "coréen",
    "zh": "chinois",
    "en": "anglais",
    "es": "espagnol",
    "de": "allemand",
    "fr": "francais",
    "pt": "portugais",
    "ru": "russe",
    "ar": "arabe",
    "hi": "hindi",
    "th": "thai",
    "vi": "vietnamien",
    "id": "indonesien",
    "it": "italien",
    "tr": "turc",
    "pl": "polonais",
    "nl": "neerlandais",
}


def normalize_lang_code(code: str | None) -> str:
    if not code:
        return "ja"
    c = code.strip().lower().split("-")[0]
    if c == "zh-cn" or c == "cn":
        return "zh"
    return c


def resolve_ocr_language(source_lang: str, detected: str | None = None) -> str:
    """Langue effective pour l'OCR local (alignement des bulles)."""
    lang = normalize_lang_code(detected or source_lang)
    if lang == "auto":
        return "ja"
    if lang in ("ja", "ko"):
        return lang
    if lang == "zh":
        return "ja"
    return lang
