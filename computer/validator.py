# AccessOS — Action Validator
# Validates parsed action dicts before they reach the executor.
# This is the first line of defence — rejects malformed, out-of-range,
# or structurally invalid actions.

from typing import Optional

# Coordinate bounds (percent)
COORD_MIN = 0.0
COORD_MAX = 100.0

# Maximum text length for a single type() action
MAX_TYPE_LENGTH = 2000

# Allowed scroll directions
SCROLL_DIRECTIONS = {'up', 'down', 'left', 'right'}

# Allowed key names (common subset — extend as needed)
ALLOWED_KEYS = {
    'enter', 'return', 'tab', 'escape', 'esc', 'backspace', 'delete',
    'space', 'up', 'down', 'left', 'right',
    'home', 'end', 'pageup', 'pagedown',
    'f1', 'f2', 'f3', 'f4', 'f5', 'f6',
    'f7', 'f8', 'f9', 'f10', 'f11', 'f12',
    'ctrl', 'alt', 'shift', 'win', 'cmd',
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
    'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
}


def validate_action(action: dict) -> tuple[bool, str]:
    """
    Validate a parsed action dict.

    Returns:
        (True, "") if valid.
        (False, reason) if invalid.
    """
    if not isinstance(action, dict):
        return False, "Action is not a dict"

    action_type = action.get("type", "")

    # ── Actions that require a coordinate point ────────────────────────────
    if action_type in ("click", "double_click", "right_click", "move", "scroll"):
        ok, msg = _validate_point(action)
        if not ok:
            return False, msg

        if action_type == "scroll":
            direction = action.get("direction", "")
            if direction not in SCROLL_DIRECTIONS:
                return False, f"Invalid scroll direction: {direction!r}"
            amount = action.get("amount", 3)
            if not isinstance(amount, (int, float)) or amount <= 0 or amount > 50:
                return False, f"Scroll amount out of range: {amount}"

    # ── Type action ────────────────────────────────────────────────────────
    elif action_type == "type":
        text = action.get("text")
        if not isinstance(text, str) or not text:
            return False, "type() requires a non-empty 'text' field"
        if len(text) > MAX_TYPE_LENGTH:
            return False, f"type() text too long: {len(text)} chars (max {MAX_TYPE_LENGTH})"

    # ── Key press ──────────────────────────────────────────────────────────
    elif action_type == "key":
        key = action.get("key", "").lower()
        if key not in ALLOWED_KEYS:
            return False, f"Key not in allowed list: {key!r}"

    # ── Hotkey ────────────────────────────────────────────────────────────
    elif action_type == "hotkey":
        keys_str = action.get("keys", "")
        parts = [k.strip().lower() for k in keys_str.split("+") if k.strip()]
        if not parts:
            return False, "hotkey() requires a non-empty 'keys' field"
        for k in parts:
            if k not in ALLOWED_KEYS:
                return False, f"Hotkey contains disallowed key: {k!r}"

    # ── Switch window ─────────────────────────────────────────────────────
    elif action_type == "switch_window":
        name = action.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return False, "switch_window() requires a non-empty 'name' field"
        forbidden = [';', '&&', '||', '|', '`', '$', '>', '<', '\n']
        for ch in forbidden:
            if ch in name:
                return False, "switch_window() name contains forbidden character: {!r}".format(ch)

    # ── Open app ──────────────────────────────────────────────────────────
    elif action_type == "open_app":
        name = action.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return False, "open_app() requires a non-empty 'name' field"
        # Basic sanity: name must not look like a shell command
        forbidden = [';', '&&', '||', '|', '`', '$', '>', '<', '\n']
        for ch in forbidden:
            if ch in name:
                return False, f"open_app() name contains forbidden character: {ch!r}"

    # ── Wait ──────────────────────────────────────────────────────────────
    elif action_type == "wait":
        seconds = action.get("seconds", 1)
        if not isinstance(seconds, (int, float)) or seconds <= 0 or seconds > 30:
            return False, f"wait() seconds out of range: {seconds}"

    # ── Terminal actions ───────────────────────────────────────────────────
    elif action_type in ("done", "fail"):
        pass  # No extra validation needed

    else:
        return False, f"Unknown action type: {action_type!r}"

    return True, ""


def _validate_point(action: dict) -> tuple[bool, str]:
    """Check that action['point'] is [x, y] with x,y in [0, 100]."""
    point = action.get("point")
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return False, f"Action {action['type']!r} requires point=[x, y]"
    x, y = point
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return False, "Coordinates must be numbers"
    if not (COORD_MIN <= x <= COORD_MAX) or not (COORD_MIN <= y <= COORD_MAX):
        return False, f"Coordinates out of range: [{x}, {y}] (must be 0–100)"
    return True, ""
