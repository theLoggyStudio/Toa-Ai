import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def configure_utf8_stdio() -> None:
    """Evite UnicodeEncodeError (█, etc.) sur la console Windows (cp1252)."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


configure_utf8_stdio()

# override=True : relit .env après chaque modification.
load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"

# Tarif : forfait de base + montant par bulle (ex. 200 + 25 × n).
PRICE_BASE_CFA = int(os.getenv("PRICE_BASE_CFA", "200"))
PRICE_PER_BUBBLE_CFA = int(
    os.getenv("PRICE_PER_BUBBLE_CFA", os.getenv("PRICE_PER_PAGE_CFA", "25"))
)


def amount_cfa_for_bubbles(bubble_count: int) -> int:
    """Montant total = forfait de base + (bulles × tarif unitaire)."""
    n = max(0, int(bubble_count))
    return PRICE_BASE_CFA + n * PRICE_PER_BUBBLE_CFA


# Estimation rapide à l'upload (sans appel Cursor) ; ajustée après traduction réelle.
ESTIMATED_BUBBLES_PER_PAGE = max(1, int(os.getenv("ESTIMATED_BUBBLES_PER_PAGE", "4")))


def estimate_bubbles_for_pages(page_count: int) -> int:
    return max(1, page_count * ESTIMATED_BUBBLES_PER_PAGE)


# Fresco — restauration photo : 250–1000 FCFA selon les mégapixels.
ECLAT_PRICE_MIN_CFA = int(os.getenv("ECLAT_PRICE_MIN_CFA", "250"))
ECLAT_PRICE_MAX_CFA = int(os.getenv("ECLAT_PRICE_MAX_CFA", "1000"))
ECLAT_MP_MIN = float(os.getenv("ECLAT_MP_MIN", "0.3"))
ECLAT_MP_MAX = float(os.getenv("ECLAT_MP_MAX", "12"))


def amount_cfa_for_image_size(width: int, height: int) -> int:
    """Prix Fresco linéaire selon les mégapixels, borné entre min et max FCFA."""
    w = max(1, int(width))
    h = max(1, int(height))
    mp = (w * h) / 1_000_000.0
    span_mp = max(1e-6, ECLAT_MP_MAX - ECLAT_MP_MIN)
    t = (mp - ECLAT_MP_MIN) / span_mp
    t = max(0.0, min(1.0, t))
    amount = round(ECLAT_PRICE_MIN_CFA + t * (ECLAT_PRICE_MAX_CFA - ECLAT_PRICE_MIN_CFA))
    return max(ECLAT_PRICE_MIN_CFA, min(ECLAT_PRICE_MAX_CFA, amount))

# Traitement par lots (pages max par passe Cursor + PDF partiel).
BATCH_PAGE_SIZE = int(os.getenv("BATCH_PAGE_SIZE", "5"))
# Pages traduites/rendues en parallèle dans un lot (appels Cursor = surtout de l'attente réseau).
PIPELINE_PAGE_CONCURRENCY = max(1, int(os.getenv("PIPELINE_PAGE_CONCURRENCY", "3")))
# Tâches traitées simultanément (au-delà : file d'attente).
PIPELINE_MAX_CONCURRENT_TASKS = max(
    1, int(os.getenv("PIPELINE_MAX_CONCURRENT_TASKS", "1"))
)
# Tentatives Cursor par page avant repli sur la page originale non traduite.
PAGE_TRANSLATION_ATTEMPTS = max(1, int(os.getenv("PAGE_TRANSLATION_ATTEMPTS", "2")))

# Mascotte Toa (Chibie) : désactivée pour l'instant, le code reste en place.
# Remettre CHIBIE_ENABLED=true dans .env pour la réactiver.
CHIBIE_ENABLED = os.getenv("CHIBIE_ENABLED", "false").lower() in (
    "true",
    "1",
    "yes",
)
DISABLE_PAYMENT = os.getenv("DISABLE_PAYMENT", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Traduction : test | production
PAYDUNYA_MODE = os.getenv("PAYDUNYA_MODE", "test")
# Fresco (restauration) : indépendant — défaut sandbox
PAYDUNYA_FRESCO_MODE = os.getenv("PAYDUNYA_FRESCO_MODE", "test")
PAYDUNYA_MASTER_KEY = os.getenv("PAYDUNYA_MASTER_KEY", "")

# Noms explicites par environnement (test / production), avec fallback legacy.
PAYDUNYA_TEST_PUBLIC_KEY = os.getenv("PAYDUNYA_TEST_PUBLIC_KEY", "")
PAYDUNYA_TEST_PRIVATE_KEY = os.getenv(
    "PAYDUNYA_TEST_PRIVATE_KEY", os.getenv("PAYDUNYA_PRIVATE_KEY", "")
)
PAYDUNYA_TEST_TOKEN = os.getenv(
    "PAYDUNYA_TEST_TOKEN", os.getenv("PAYDUNYA_TOKEN_TEST", "")
)

PAYDUNYA_PROD_PUBLIC_KEY = os.getenv("PAYDUNYA_PROD_PUBLIC_KEY", "")
PAYDUNYA_PROD_PRIVATE_KEY = os.getenv(
    "PAYDUNYA_PROD_PRIVATE_KEY", os.getenv("PAYDUNYA_PRIVATE_KEY", "")
)
PAYDUNYA_PROD_TOKEN = os.getenv(
    "PAYDUNYA_PROD_TOKEN", os.getenv("PAYDUNYA_TOKEN", "")
)

if PAYDUNYA_MODE == "production":
    PAYDUNYA_PUBLIC_KEY = PAYDUNYA_PROD_PUBLIC_KEY
    PAYDUNYA_PRIVATE_KEY = PAYDUNYA_PROD_PRIVATE_KEY
    PAYDUNYA_TOKEN = PAYDUNYA_PROD_TOKEN
else:
    PAYDUNYA_PUBLIC_KEY = PAYDUNYA_TEST_PUBLIC_KEY
    PAYDUNYA_PRIVATE_KEY = PAYDUNYA_TEST_PRIVATE_KEY
    PAYDUNYA_TOKEN = PAYDUNYA_TEST_TOKEN


def paydunya_mode_for_kind(kind: str) -> str:
    """Mode PayDunya selon le produit (traduction vs Fresco)."""
    if kind == "restore":
        return PAYDUNYA_FRESCO_MODE
    return PAYDUNYA_MODE


def paydunya_credentials_for_mode(mode: str) -> tuple[str, str, str]:
    """Retourne (private_key, token, create_api_url) pour le mode donné."""
    if mode == "production":
        return (
            PAYDUNYA_PROD_PRIVATE_KEY,
            PAYDUNYA_PROD_TOKEN,
            "https://app.paydunya.com/api/v1/checkout-invoice/create",
        )
    return (
        PAYDUNYA_TEST_PRIVATE_KEY,
        PAYDUNYA_TEST_TOKEN,
        "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/create",
    )

FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "3100"))
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "9400"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", f"http://localhost:{FRONTEND_PORT}")
BACKEND_PUBLIC_URL = os.getenv(
    "BACKEND_PUBLIC_URL", f"http://127.0.0.1:{BACKEND_PORT}"
)

CURSOR_API_KEY = os.getenv("CURSOR_API_KEY", "")
CURSOR_MODEL = os.getenv("CURSOR_MODEL", "auto")
CURSOR_WORKSPACE_DIR = os.getenv("CURSOR_WORKSPACE_DIR", str(BASE_DIR.parent))
CURSOR_USE_CLOUD = os.getenv("CURSOR_USE_CLOUD", "true").lower() in (
    "true",
    "1",
    "yes",
)
CURSOR_MAX_IMAGE_PX = int(os.getenv("CURSOR_MAX_IMAGE_PX", "1920"))
CURSOR_PAGE_DELAY_SEC = float(os.getenv("CURSOR_PAGE_DELAY_SEC", "0.5"))

# URL create par défaut (traduction) — Fresco peut utiliser le sandbox à part.
_, _, PAYDUNYA_API_URL = paydunya_credentials_for_mode(PAYDUNYA_MODE)

for directory in (UPLOAD_DIR, OUTPUT_DIR):
    directory.mkdir(parents=True, exist_ok=True)
