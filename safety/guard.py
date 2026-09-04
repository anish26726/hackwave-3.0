# AccessOS — Safety Guard
# Sits between agent/brain.py output and computer/executor.py.
# Identifies sensitive actions that require user confirmation.

import re

# ── Sensitive keyword patterns ─────────────────────────────────────────────
# If the action's text/message/name contains any of these, we flag it.
SENSITIVE_PATTERNS = [
    r'\bsubmit\b', r'\bdelete\b', r'\bremove\b', r'\bsend\b',
    r'\bpurchase\b', r'\bbuy\b', r'\bpay\b', r'\binstall\b',
    r'\buninstall\b', r'\bformat\b', r'\bconfirm\b', r'\bapply\b',
    r'\bsave password\b', r'\bsign out\b', r'\blog out\b', r'\bdeactivate\b',
    r'\bshutdown\b', r'\brestart\b', r'\bwipe\b', r'\berase\b',
]

# Compile once
_COMPILED = [re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATTERNS]


class ActionBlocked(Exception):
    """Raised when an action is blocked by the safety layer."""
    pass


def is_sensitive(action: dict) -> tuple[bool, str]:
    """
    Check if an action requires user confirmation.

    Returns:
        (True, reason_string) if confirmation is required.
        (False, "") if the action is safe to execute immediately.
    """
    action_type = action.get("type", "")

    # file delete is always sensitive
    if action_type == "delete_file":
        return True, "This will permanently delete a file."

    # Check all string values in the action dict for sensitive keywords
    text_to_check = " ".join(
        str(v) for v in action.values() if isinstance(v, str)
    ).lower()

    for pattern in _COMPILED:
        if pattern.search(text_to_check):
            return True, f"Action may be sensitive (matched: {pattern.pattern!r})"

    return False, ""


def check_action(action: dict) -> tuple[bool, str]:
    """
    Full safety check for a single action.

    Returns:
        (True, "") if action is clear.
        (False, reason) if action should be blocked or needs confirmation.
    """
    # Hard-block: model must never produce these
    HARD_BLOCKED = {
        'shell', 'powershell', 'cmd_exec', 'eval', 'exec',
        'run_script', 'execute_code', 'run_python',
    }
    if action.get("type") in HARD_BLOCKED:
        return False, f"Hard-blocked action type: {action['type']!r}"

    return True, ""
