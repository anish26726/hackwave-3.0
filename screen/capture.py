# AccessOS -- Screen Capture
# Captures the current screen and returns a base64-encoded JPEG string
# suitable for embedding in API requests.
# Uses mss as the primary capture method, falls back to Pillow ImageGrab
# if mss is denied access (common in certain Windows terminal environments).

import io
import base64
from PIL import Image, ImageGrab
import mss

from config.settings import SCREENSHOT_QUALITY, MAX_IMAGE_WIDTH, MONITOR_INDEX


def capture_screen() -> str:
    """
    Capture the monitor specified by MONITOR_INDEX and return a
    base64-encoded JPEG string.

    Returns:
        str: Base64-encoded JPEG image data (no data URI prefix).

    Raises:
        RuntimeError: If the screen cannot be captured by any method.
    """
    img = None
    last_error = None

    # Method 1: mss (fast, low overhead)
    try:
        with mss.mss() as sct:
            # L6 fix: respect MONITOR_INDEX from settings (default 1 = primary)
            monitors = sct.monitors
            idx = min(MONITOR_INDEX, len(monitors) - 1)  # clamp to valid range
            if idx < 1:
                idx = 1
            monitor = monitors[idx]
            raw = sct.grab(monitor)
            img = Image.frombytes('RGB', raw.size, raw.bgra, 'raw', 'BGRX')
    except Exception as e:
        last_error = e
        img = None

    # Method 2: Pillow ImageGrab (fallback, works in most Windows environments)
    if img is None:
        try:
            img = ImageGrab.grab()
            img = img.convert('RGB')
        except Exception as e2:
            raise RuntimeError(
                "[screen/capture] All screenshot methods failed.\n"
                "  mss error: {}\n  ImageGrab error: {}".format(last_error, e2)
            )

    # Resize if the screen is very wide to reduce token/bandwidth usage
    if img.width > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / img.width
        new_height = int(img.height * ratio)
        img = img.resize((MAX_IMAGE_WIDTH, new_height), Image.LANCZOS)

    # Encode to JPEG in memory
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=SCREENSHOT_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return encoded


def get_screen_size() -> tuple:
    """Return (width, height) of the monitor specified by MONITOR_INDEX."""
    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            idx = min(MONITOR_INDEX, len(monitors) - 1)
            if idx < 1:
                idx = 1
            m = monitors[idx]
            return m['width'], m['height']
    except Exception:
        # Fallback using Pillow
        img = ImageGrab.grab()
        return img.width, img.height
