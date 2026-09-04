# AccessOS -- Configuration
# Loads environment variables and exposes typed settings.

import os
from dotenv import load_dotenv

# Load .env from the project root (one level up from config/)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


def get_api_key() -> str:
    """
    Return the Featherless API key at runtime.
    Raises EnvironmentError with a clear message if missing or still a placeholder.
    """
    value = os.environ.get('FEATHERLESS_API_KEY', '').strip()
    if not value or value == 'your_featherless_api_key_here':
        raise EnvironmentError(
            "[AccessOS] FEATHERLESS_API_KEY is not set.\n"
            "  -> Open .env and set FEATHERLESS_API_KEY=<your key>"
        )
    return value


# ---- Featherless / UI-TARS -----------------------------------------------
# API key is fetched lazily via get_api_key() to avoid crashing at import time.
FEATHERLESS_API_KEY: str = os.environ.get('FEATHERLESS_API_KEY', '')
FEATHERLESS_MODEL: str = os.environ.get(
    'FEATHERLESS_MODEL', 'ByteDance-Seed/UI-TARS-1.5-7B'
)
FEATHERLESS_BASE_URL: str = os.environ.get(
    'FEATHERLESS_BASE_URL', 'https://api.featherless.ai/v1'
)

# ── Agent behaviour ────────────────────────────────────────────────────────
MAX_ACTIONS_PER_TASK: int = 30        # Hard cap per task to prevent infinite loops
ACTION_TIMEOUT_SECONDS: int = 10      # Seconds to wait before an action times out
API_TIMEOUT_SECONDS: int = 60         # HTTP timeout for Featherless API calls
MAX_RETRIES: int = 3                  # Retry limit for API calls

# ── Screen capture ─────────────────────────────────────────────────────────
SCREENSHOT_QUALITY: int = 85          # JPEG quality for screenshots sent to API (1-95)
MAX_IMAGE_WIDTH: int = 1280           # Resize large screens before sending

# ── Multi-monitor support (L6 fix) ─────────────────────────────────────────
# 1 = primary monitor, 2 = second monitor, etc.
# Set MONITOR_INDEX in .env to capture a different monitor.
MONITOR_INDEX: int = int(os.environ.get('MONITOR_INDEX', '1'))

# ── Session context (L3 fix) ───────────────────────────────────────────────
# How many past task results to keep in session memory for follow-up commands.
SESSION_CONTEXT_SIZE: int = int(os.environ.get('SESSION_CONTEXT_SIZE', '5'))

# ── Tesseract OCR (L5) ─────────────────────────────────────────────────────
# Optional: set TESSERACT_CMD in .env if Tesseract is not on your PATH.
# Example: TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSERACT_CMD: str = os.environ.get('TESSERACT_CMD', '')
