# AccessOS — Intelligent Screen Reader (Phase 4)
# Reads and organizes the content currently visible on the user's screen.
#
# Priority chain (as per plan):
#   1. Windows native accessibility info (foreground window title, focused element)
#   2. OCR via pytesseract (raw visible text)
#   3. UI-TARS vision description (layout + context understanding)
#
# Returns clean, organised, readable text ready for TTS.

import re
import ctypes
import ctypes.wintypes
from typing import Optional
from PIL import Image

try:
    import pytesseract
    # Quick probe — will raise if Tesseract engine binary is missing
    pytesseract.get_tesseract_version()
    _TESSERACT_AVAILABLE = True
except Exception:
    _TESSERACT_AVAILABLE = False

try:
    import pygetwindow as gw
    _PYGETWINDOW_AVAILABLE = True
except ImportError:
    _PYGETWINDOW_AVAILABLE = False


# ── OCR noise cleanup ──────────────────────────────────────────────────────
_MULTISPACE = re.compile(r'[ \t]{2,}')
_MULTILINE  = re.compile(r'\n{3,}')
_JUNK_LINE  = re.compile(r'^[\W_]{1,3}$')   # lines with only punctuation/symbols


# ── Windows accessibility helpers ─────────────────────────────────────────

def get_foreground_window_title() -> str:
    """Return the title of the currently focused window (zero external deps)."""
    try:
        hwnd   = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf    = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def get_open_windows() -> list[str]:
    """Return titles of all visible top-level windows (uses pygetwindow if available)."""
    if not _PYGETWINDOW_AVAILABLE:
        return []
    try:
        return [w.title for w in gw.getAllWindows() if w.title.strip()]
    except Exception:
        return []


# ── OCR ───────────────────────────────────────────────────────────────────

def ocr_image(pil_image: Image.Image) -> Optional[str]:
    """
    Run Tesseract OCR on *pil_image*.
    Returns cleaned text or None if Tesseract is not available.
    """
    if not _TESSERACT_AVAILABLE:
        return None
    try:
        # PSM 6 = assume a single uniform block of text (good for full-screen)
        raw = pytesseract.image_to_string(pil_image, config='--psm 6')
        return _clean_ocr(raw)
    except Exception as e:
        print(f"[reader] OCR error: {e}")
        return None


def _clean_ocr(raw: str) -> str:
    """Strip OCR artefacts and normalise whitespace."""
    lines = []
    for line in raw.splitlines():
        line = _MULTISPACE.sub(' ', line).strip()
        if len(line) <= 1 or _JUNK_LINE.match(line):
            continue
        lines.append(line)
    out = '\n'.join(lines)
    return _MULTILINE.sub('\n\n', out).strip()


# ── Screen reader class ────────────────────────────────────────────────────

class ScreenReader:
    """
    High-level intelligent screen reading.

    Usage:
        reader = ScreenReader()
        text   = reader.read(pil_img, b64_img, user_query)
    """

    def read(
        self,
        screenshot_pil: Image.Image,
        screenshot_b64: str,
        user_query: str = "Read the screen",
    ) -> str:
        """
        Produce an organised, human-readable description of the current screen.

        1. Window context (what app is open)
        2. OCR text (raw visible characters)
        3. UI-TARS vision description (layout, buttons, context)

        Returns a single string suitable for TTS and display.
        """
        sections: list[str] = []

        # 1 — Window / application context
        title = get_foreground_window_title()
        if title:
            sections.append(f"Active window: {title}")

        # 2 — OCR: extract raw visible text
        ocr_text = ocr_image(screenshot_pil)
        if ocr_text and len(ocr_text) > 10:
            sections.append("Visible text:\n" + ocr_text)
        elif not _TESSERACT_AVAILABLE:
            sections.append(
                "(Tip: install Tesseract OCR for full text extraction — "
                "see https://github.com/UB-Mannheim/tesseract/wiki)"
            )

        # 3 — UI-TARS vision: contextual understanding
        vision = self._uitars_description(screenshot_b64, user_query)
        if vision:
            sections.append("Screen description:\n" + vision)

        if not sections:
            return "Could not read any content from the screen."

        return "\n\n".join(sections)

    # ── Internal ────────────────────────────────────────────────────────

    def _uitars_description(
        self, screenshot_b64: str, user_query: str
    ) -> Optional[str]:
        """Request a screen description from UI-TARS (read mode, not action mode)."""
        try:
            from agent.brain import describe_screen
            return describe_screen(screenshot_b64, user_query)
        except Exception as e:
            print(f"[reader] UI-TARS vision failed: {e}")
            return None


# ── Intent detection ──────────────────────────────────────────────────────

# Keywords that indicate the user wants the screen READ rather than an action.
_READ_TRIGGERS = {
    "read my screen", "read the screen", "read this page", "read this",
    "read the page", "what's on my screen", "what is on my screen",
    "what do you see", "what can you see", "describe this",
    "describe the screen", "describe what you see", "what's the error",
    "what is the error", "read the error", "read the buttons",
    "what's happening", "what is happening", "tell me what's on screen",
    "screen reader", "read screen",
}

_READ_PATTERNS = re.compile(
    r'\b(read|describe|what(?:\'s| is) (on|the)|tell me what)\b',
    re.IGNORECASE,
)


def is_screen_read_command(text: str) -> bool:
    """
    Return True if *text* is a screen-reading request (not a computer action).

    Checks exact phrase matches first, then regex patterns.
    """
    t = text.lower().strip()
    if any(trigger in t for trigger in _READ_TRIGGERS):
        return True
    if _READ_PATTERNS.search(t):
        return True
    return False


# ── Module-level singleton ─────────────────────────────────────────────────

_reader: Optional[ScreenReader] = None


def get_reader() -> ScreenReader:
    global _reader
    if _reader is None:
        _reader = ScreenReader()
    return _reader
