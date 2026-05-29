import os
from pathlib import Path

from dotenv import load_dotenv

# override=True : relit .env après chaque modification (évite OCR_FAST_MODE bloqué)
load_dotenv(override=True)


def is_ocr_fast_mode() -> bool:
    load_dotenv(override=True)
    return os.getenv("OCR_FAST_MODE", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def is_ocr_deep_mode() -> bool:
    load_dotenv(override=True)
    return os.getenv("OCR_DEEP_MODE", "true").lower() in (
        "true",
        "1",
        "yes",
    )

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"

# Nouveau tarif: par bulle traduite (fallback legacy: PRICE_PER_PAGE_CFA).
PRICE_PER_BUBBLE_CFA = int(
    os.getenv("PRICE_PER_BUBBLE_CFA", os.getenv("PRICE_PER_PAGE_CFA", "75"))
)

MAX_BLOCKS_PER_PAGE = int(os.getenv("MAX_BLOCKS_PER_PAGE", "50"))

# Mode test : pas de manga-ocr (évite téléchargement HF 500+ Mo)
OCR_FAST_MODE = is_ocr_fast_mode()

DISABLE_PAYMENT = os.getenv("DISABLE_PAYMENT", "false").lower() in (
    "true",
    "1",
    "yes",
)

PAYDUNYA_MODE = os.getenv("PAYDUNYA_MODE", "test")
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

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")


def get_frontend_origins() -> list[str]:
    """Origines CORS (FRONTEND_ORIGIN peut contenir plusieurs URLs séparées par des virgules)."""
    raw = os.getenv("FRONTEND_ORIGINS") or FRONTEND_ORIGIN
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return origins or ["http://localhost:5173"]
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "http://127.0.0.1:8000")

CURSOR_API_KEY = os.getenv("CURSOR_API_KEY", "")
CURSOR_MODEL = os.getenv("CURSOR_MODEL", "auto")
CURSOR_WORKSPACE_DIR = os.getenv("CURSOR_WORKSPACE_DIR", str(BASE_DIR.parent))

PAYDUNYA_API_URL = "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/create"
if PAYDUNYA_MODE == "production":
    PAYDUNYA_API_URL = "https://app.paydunya.com/api/v1/checkout-invoice/create"

for directory in (UPLOAD_DIR, OUTPUT_DIR):
    directory.mkdir(parents=True, exist_ok=True)
