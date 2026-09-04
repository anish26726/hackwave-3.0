# AccessOS — Action Executor
# Translates validated action dicts into real Windows computer-control actions.
# Uses pyautogui (mouse/keyboard) and controlled app-launching via the Win32
# shell API — NO subprocess, shell, eval, exec, or os.system.

import os
import time
import pyautogui

try:
    import pygetwindow as gw
    _PYGETWINDOW_AVAILABLE = True
except ImportError:
    _PYGETWINDOW_AVAILABLE = False

from screen.capture import get_screen_size

# Safety net: pyautogui will raise an exception if the mouse hits a screen corner
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05  # Small pause between pyautogui calls for stability


# ── Application allow-list ─────────────────────────────────────────────────
# Maps friendly names to their Windows executable paths / commands.
# Paths are resolved at call time via os.path.expandvars() so that
# %USERNAME%, %LOCALAPPDATA%, etc. are substituted correctly.
# Multiple candidate paths can be given as a list; the first that exists wins.
APP_ALLOW_LIST: dict[str, object] = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ],
    "google chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
    "edge":           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "microsoft edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "notepad":         "notepad.exe",
    "word":            r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
    "excel":           r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
    "file explorer":   "explorer.exe",
    "explorer":        "explorer.exe",
    "calculator":      "calc.exe",
    "paint":           "mspaint.exe",
    "task manager":    "taskmgr.exe",
    "settings":        "ms-settings:",
    "cmd":             "cmd.exe",           # Controlled launch only
    "terminal":        "wt.exe",
    "vs code": [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"C:\Program Files\Microsoft VS Code\Code.exe",
    ],
    "vscode": [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"C:\Program Files\Microsoft VS Code\Code.exe",
    ],
}


def execute_action(action: dict) -> str:
    """
    Execute a validated action dict on the local Windows machine.

    Args:
        action: Validated action dict from computer/validator.py.

    Returns:
        Human-readable status string describing what happened.

    Raises:
        ValueError: If the action type is unknown (should be caught by validator first).
    """
    action_type = action["type"]
    screen_w, screen_h = get_screen_size()

    try:
        if action_type in ("click", "double_click", "right_click", "move"):
            x_px, y_px = _percent_to_px(action["point"], screen_w, screen_h)
            if action_type == "click":
                pyautogui.click(x_px, y_px)
                return f"Clicked at ({x_px}, {y_px})"
            elif action_type == "double_click":
                pyautogui.doubleClick(x_px, y_px)
                return f"Double-clicked at ({x_px}, {y_px})"
            elif action_type == "right_click":
                pyautogui.rightClick(x_px, y_px)
                return f"Right-clicked at ({x_px}, {y_px})"
            elif action_type == "move":
                pyautogui.moveTo(x_px, y_px, duration=0.2)
                return f"Moved mouse to ({x_px}, {y_px})"

        elif action_type == "scroll":
            # point is optional — default to screen centre if omitted.
            if "point" in action:
                x_px, y_px = _percent_to_px(action["point"], screen_w, screen_h)
            else:
                x_px, y_px = screen_w // 2, screen_h // 2
            direction = action.get("direction", "down")
            amount = int(action.get("amount", 3))
            clicks = amount if direction in ("up", "left") else -amount
            if direction in ("up", "down"):
                pyautogui.scroll(clicks, x=x_px, y=y_px)
            else:
                pyautogui.hscroll(clicks, x=x_px, y=y_px)
            return f"Scrolled {direction} {amount} at ({x_px}, {y_px})"

        elif action_type == "type":
            text = action["text"]
            pyautogui.write(text, interval=0.02)
            preview = text[:50] + "…" if len(text) > 50 else text
            return f"Typed: {preview!r}"

        elif action_type == "key":
            key = action["key"].lower()
            pyautogui.press(key)
            return f"Pressed key: {key}"

        elif action_type == "hotkey":
            # Accept 'keys' field (preferred) or 'key' as fallback.
            keys_raw = action.get("keys") or action.get("key", "")
            keys = [k.strip().lower() for k in keys_raw.split("+") if k.strip()]
            pyautogui.hotkey(*keys)
            return f"Hotkey: {'+'.join(keys)}"

        elif action_type == "switch_window":
            return _switch_window(action.get("name", ""))

        elif action_type == "open_app":
            return _open_app(action.get("name", ""))

        elif action_type == "wait":
            seconds = float(action.get("seconds", 1))
            time.sleep(min(seconds, 10))  # Cap at 10s for safety
            return f"Waited {seconds}s"

        elif action_type == "done":
            return f"DONE: {action.get('message', 'Task completed.')}"

        elif action_type == "fail":
            return f"FAIL: {action.get('reason', 'Unknown failure.')}"

        else:
            raise ValueError(f"Unhandled action type: {action_type!r}")

    except pyautogui.FailSafeException:
        raise  # Let the main loop handle emergency stop
    except Exception as e:
        return f"Executor error for {action_type!r}: {e}"


def _percent_to_px(point: list, screen_w: int, screen_h: int) -> tuple[int, int]:
    """Convert [x_percent, y_percent] → absolute pixel coordinates."""
    x_px = int((point[0] / 100.0) * screen_w)
    y_px = int((point[1] / 100.0) * screen_h)
    return x_px, y_px


def _open_app(name: str) -> str:
    """
    Open an application by friendly name using the controlled allow-list.
    Uses the Windows shell to open the app — NOT arbitrary subprocess calls.

    APP_ALLOW_LIST values may be a single path string or a list of candidate
    paths; the first candidate that exists on disk is used.
    """
    key = name.strip().lower()
    entry = APP_ALLOW_LIST.get(key)
    if not entry:
        # Try partial match
        for app_name, app_entry in APP_ALLOW_LIST.items():
            if key in app_name or app_name in key:
                entry = app_entry
                break

    if not entry:
        return f"App {name!r} not in allow-list. Cannot open."

    # Resolve a single path string or iterate through a list of candidates.
    candidates = entry if isinstance(entry, list) else [entry]
    path = None
    for candidate in candidates:
        resolved = os.path.expandvars(candidate)
        # ms-settings: and similar URI schemes don't exist as files — use them directly.
        if ":" in resolved and not resolved.endswith(".exe"):
            path = resolved
            break
        if os.path.isfile(resolved):
            path = resolved
            break

    if not path:
        # Last resort: try the first candidate anyway (system PATH executables like notepad.exe)
        path = os.path.expandvars(candidates[0])

    try:
        os.startfile(path)
        time.sleep(1.5)  # Give the app time to open
        return f"Opened: {name}"
    except Exception as e:
        return f"Failed to open {name!r}: {e}"


def _switch_window(name: str) -> str:
    """
    Bring an existing window to the foreground by matching its title.
    Uses pygetwindow (safe Windows API) -- no shell commands.
    """
    if not _PYGETWINDOW_AVAILABLE:
        # Fallback: Alt+Tab cycle
        pyautogui.hotkey("alt", "tab")
        time.sleep(0.5)
        return "Window switch attempted via Alt+Tab (pygetwindow not available)"

    name_lower = name.strip().lower()
    try:
        all_windows = gw.getAllWindows()
        matches = [w for w in all_windows
                   if name_lower in w.title.lower() and w.title.strip()]
        if not matches:
            # Fallback to Alt+Tab
            pyautogui.hotkey("alt", "tab")
            time.sleep(0.5)
            return "No window matching '{}' found. Used Alt+Tab.".format(name)

        target = matches[0]
        target.activate()
        time.sleep(0.5)
        return "Switched to window: '{}'".format(target.title)
    except Exception as e:
        # Last resort fallback
        try:
            pyautogui.hotkey("alt", "tab")
            time.sleep(0.5)
            return "Window switch fallback (Alt+Tab): {}".format(e)
        except Exception:
            return "Window switch failed: {}".format(e)
