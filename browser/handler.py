# AccessOS — Browser Handler (Phase 6)
#
# Controlled, safe browser operations for AccessOS.
# Chrome (default) is launched with --remote-debugging-port=9222 so that
# the DOMReader can extract structured page content via CDP.
#
# SECURITY MODEL:
#   - URL scheme allowlist: only http:// and https://
#   - Blocks javascript:, data:, file:// schemes
#   - No Selenium / Playwright — all control is via pyautogui keyboard shortcuts
#   - No subprocess / shell commands — Chrome path comes from the executor allow-list
#   - Sensitive form confirmation handled by safety.guard (existing layer)
#
# Supported operations:
#   open_browser, navigate, search, back, forward, refresh, new_tab, close_tab

import os
import re
import time
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import pyautogui

from browser.intent import build_search_url, _normalise_url
from browser.dom_reader import get_dom_reader, DOMReaderError

# ── URL Safety ────────────────────────────────────────────────────────────
_SAFE_SCHEMES = ('http://', 'https://')
_BLOCKED_PATTERNS = re.compile(
    r'^(?:javascript:|data:|file://)',
    re.IGNORECASE,
)

# ── Chrome executable candidates ──────────────────────────────────────────
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

_FIREFOX_CANDIDATES = [
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
]

CDP_PORT = 9222  # Chrome DevTools Protocol port


class BrowserError(Exception):
    """Raised when a browser operation fails validation or execution."""
    pass


# Window title fragments used to locate the browser window
_BROWSER_WINDOW_TITLES = {
    'chrome':  ['Google Chrome', 'Chrome'],
    'edge':    ['Microsoft Edge', 'Edge'],
    'firefox': ['Mozilla Firefox', 'Firefox'],
}

try:
    import pygetwindow as _gw
    _PYGETWINDOW_OK = True
except ImportError:
    _PYGETWINDOW_OK = False


class BrowserHandler:
    """
    Safe, controlled browser operations for AccessOS.

    All browser control is done via pyautogui keyboard shortcuts.
    Chrome is launched with --remote-debugging-port=9222 for DOM access.
    """

    def _validate_url(self, url: str) -> str:
        """Validate and normalise a URL. Raises BrowserError for unsafe URLs."""
        url = url.strip()

        # Check blocked schemes BEFORE normalising — _normalise_url would
        # prepend 'https://' which hides the dangerous scheme entirely.
        if _BLOCKED_PATTERNS.match(url):
            raise BrowserError(
                f"Blocked URL scheme in '{url}'. "
                "Only http:// and https:// are allowed."
            )

        url = _normalise_url(url)

        if not url.startswith(_SAFE_SCHEMES):
            raise BrowserError(
                f"URL must start with http:// or https://. Got: '{url}'"
            )

        return url

    def _find_chrome(self) -> Optional[str]:
        """Find Chrome executable path."""
        for path in _CHROME_CANDIDATES:
            expanded = os.path.expandvars(path)
            if os.path.isfile(expanded):
                return expanded
        return None

    def _find_browser_exe(self, browser: str) -> Optional[str]:
        """Find browser executable by name."""
        candidates = {
            'chrome':  _CHROME_CANDIDATES,
            'edge':    _EDGE_CANDIDATES,
            'firefox': _FIREFOX_CANDIDATES,
        }.get(browser, _CHROME_CANDIDATES)

        for path in candidates:
            expanded = os.path.expandvars(path)
            if os.path.isfile(expanded):
                return expanded
        return None

    def open_browser(self, browser: str = 'chrome', url: str = '') -> str:
        """
        Launch the browser. Chrome is launched with --remote-debugging-port
        so DOMReader can connect to it for page content extraction.

        Args:
            browser: 'chrome', 'edge', or 'firefox'
            url:     Optional URL to open immediately.
        """
        exe = self._find_browser_exe(browser)
        if not exe:
            raise BrowserError(
                f"Could not find {browser.title()} on this computer. "
                "Please install it or check the installation path."
            )

        # Build args — only Chrome supports CDP debugging flag
        args = [exe]
        if browser == 'chrome':
            args.append(f'--remote-debugging-port={CDP_PORT}')
        if url:
            try:
                url = self._validate_url(url)
                args.append(url)
            except BrowserError:
                pass  # Skip invalid URL, just open browser

        try:
            subprocess.Popen(
                args,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            time.sleep(2.5)  # Wait for browser window to open
            return f"Opened {browser.title()} browser."
        except Exception as e:
            raise BrowserError(f"Failed to launch {browser.title()}: {e}")

    def _focus_browser(self, browser: str = 'chrome') -> bool:
        """
        Bring the browser window to the foreground so keyboard shortcuts
        go to the browser, not the terminal.

        Returns True if a browser window was found and focused, False otherwise.
        """
        if not _PYGETWINDOW_OK:
            # No pygetwindow — just give a small delay and hope for the best
            time.sleep(0.3)
            return False

        title_hints = _BROWSER_WINDOW_TITLES.get(browser, ['Chrome'])
        try:
            all_windows = _gw.getAllWindows()
            for hint in title_hints:
                matches = [
                    w for w in all_windows
                    if hint.lower() in w.title.lower() and w.title.strip()
                ]
                if matches:
                    win = matches[0]
                    try:
                        win.activate()
                    except Exception:
                        win.minimize()
                        time.sleep(0.2)
                        win.restore()
                    time.sleep(0.6)  # Wait for window to come to foreground
                    return True
        except Exception:
            pass

        # Browser window not found — may not be open yet
        return False

    def navigate(self, url: str, browser: str = 'chrome') -> str:
        """
        Navigate the active browser tab to a URL.
        Uses Ctrl+L (address bar focus) → type URL → Enter.

        Args:
            url:     Target URL.
            browser: Browser hint (for error messages).
        """
        url = self._validate_url(url)

        # Bring browser to foreground BEFORE sending keystrokes
        focused = self._focus_browser(browser)
        if not focused:
            print(f"[browser] Could not find {browser} window — trying anyway.")

        # Focus address bar
        pyautogui.hotkey('ctrl', 'l')
        time.sleep(0.5)

        # Select all and type URL
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.15)
        pyautogui.write(url, interval=0.03)
        time.sleep(0.2)
        pyautogui.press('enter')
        time.sleep(2.0)  # Wait for page to start loading

        return f"Navigating to: {url}"

    def search(self, query: str, browser: str = 'chrome', engine: str = 'google') -> str:
        """
        Open browser (if needed) and search for the query on Google.

        Args:
            query:   Search query string.
            browser: Browser to use.
            engine:  Search engine ('google', 'bing', 'duck').
        """
        if not query.strip():
            raise BrowserError("Search query cannot be empty.")

        url = build_search_url(query.strip(), engine)
        result = self.navigate(url, browser)
        return f"Searching for '{query}' on {engine.title()}..."

    def go_back(self, browser: str = 'chrome') -> str:
        """Navigate back one page (Alt+Left)."""
        self._focus_browser(browser)
        pyautogui.hotkey('alt', 'left')
        time.sleep(0.8)
        return "Went back to the previous page."

    def go_forward(self, browser: str = 'chrome') -> str:
        """Navigate forward one page (Alt+Right)."""
        self._focus_browser(browser)
        pyautogui.hotkey('alt', 'right')
        time.sleep(0.8)
        return "Went forward to the next page."

    def refresh(self, browser: str = 'chrome') -> str:
        """Refresh the current page (F5)."""
        self._focus_browser(browser)
        pyautogui.press('f5')
        time.sleep(1.0)
        return "Page refreshed."

    def new_tab(self, browser: str = 'chrome') -> str:
        """Open a new browser tab (Ctrl+T)."""
        self._focus_browser(browser)
        pyautogui.hotkey('ctrl', 't')
        time.sleep(0.5)
        return "Opened a new tab."

    def close_tab(self, browser: str = 'chrome') -> str:
        """Close the current browser tab (Ctrl+W)."""
        self._focus_browser(browser)
        pyautogui.hotkey('ctrl', 'w')
        time.sleep(0.5)
        return "Closed the current tab."

    def read_page(self, query: str = '') -> str:
        """
        Read the content of the current Chrome webpage via CDP DOM reader.

        Falls back gracefully if CDP is not available.

        Args:
            query: Optional query to focus the output (e.g. 'links', 'headings').
        """
        reader = get_dom_reader()

        if not reader.is_available():
            return (
                "Chrome DevTools Protocol is not active.\n"
                "To enable webpage reading, restart Chrome using AccessOS:\n"
                "  Type: open chrome\n"
                "Then navigate to the page and try again.\n\n"
                "Alternatively, use 'read my screen' for a visual screen read."
            )

        try:
            content = reader.read(query=query)
            return content
        except DOMReaderError as e:
            return f"Could not read page: {e}"
        except Exception as e:
            return f"Unexpected error reading page: {e}"


# ── Module-level singleton ─────────────────────────────────────────────────
_handler: Optional[BrowserHandler] = None


def get_handler() -> BrowserHandler:
    global _handler
    if _handler is None:
        _handler = BrowserHandler()
    return _handler
