# AccessOS — Agent Brain
# Sends screenshot + task to UI-TARS-1.5-7B via the Featherless API
# and parses the returned GUI action.

import re
import time
import requests
from typing import Optional

from config.settings import (
    get_api_key,
    FEATHERLESS_MODEL,
    FEATHERLESS_BASE_URL,
    API_TIMEOUT_SECONDS,
    MAX_RETRIES,
)

# System prompt for UI-TARS
SYSTEM_PROMPT = """You are AccessOS, a Windows GUI automation agent.
You receive a screenshot and a task. Decide the SINGLE best next action.

ACTION FORMAT (reply with exactly ONE line, nothing else):
  click(point='[x, y]')
  double_click(point='[x, y]')
  right_click(point='[x, y]')
  type(text='<text>')
  key(key='<keyname>')
  hotkey(keys='<key1>+<key2>')
  scroll(point='[x, y]', direction='up|down|left|right', amount=3)
  move(point='[x, y]')
  open_app(name='<app name>')
  switch_window(name='<window title or app name>')
  wait(seconds=1)
  done(message='<what was accomplished>')
  fail(reason='<why task cannot be completed>')

COORDINATES: [x_percent, y_percent] where [0,0]=top-left, [100,100]=bottom-right.

CRITICAL RULES:
1. If the task is already complete (you can see the result on screen), call done() IMMEDIATELY.
2. If you just opened an app and can see it on screen, call done() - do NOT open it again.
3. NEVER repeat the exact same action twice in a row. If an action did not work, try something different.
4. Do NOT write explanations, chain-of-thought, or extra text. ONE action line only.
5. If the task involved opening an app and that app is now visible, the task is done."""


def _build_messages(task: str, screenshot_b64: str, history: list[dict]) -> list[dict]:
    """Build the messages array for the chat completions API."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Include action history with results so model knows what already happened
    for entry in history:
        action_text = entry.get("action", "")
        result_text = entry.get("result", "")
        assistant_content = action_text
        if result_text:
            assistant_content = f"{action_text}  # Result: {result_text}"
        messages.append({"role": "assistant", "content": assistant_content})

        if entry.get("observation"):
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": (
                         f"After that action ({result_text}), the screen changed. "
                         f"Look at the updated screen and continue the task: {task}. "
                         f"If the task is now complete, call done()."
                     )},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{entry['observation']}"
                    }}
                ]
            })

    # Current turn
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": f"Task: {task}. What is the single next action?"},
            {"type": "image_url", "image_url": {
                "url": f"data:image/jpeg;base64,{screenshot_b64}"
            }}
        ]
    })
    return messages


def ask_uitars(task: str, screenshot_b64: str, history: list[dict] | None = None) -> Optional[dict]:
    """
    Send the current screen and task to UI-TARS via Featherless.

    Args:
        task:           The user's natural-language goal.
        screenshot_b64: Base64-encoded JPEG of the current screen.
        history:        List of previous {action, observation} dicts for this task.

    Returns:
        Parsed action dict, e.g. {"type": "click", "point": [640, 400]}
        Returns None on unrecoverable failure.
    """
    history = history or []
    messages = _build_messages(task, screenshot_b64, history)

    url = f"{FEATHERLESS_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": FEATHERLESS_MODEL,
        "messages": messages,
        "max_tokens": 256,
        "temperature": 0.0,   # Deterministic for reliability
    }

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=API_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
            raw_text = data["choices"][0]["message"]["content"].strip()
            print(f"[brain] UI-TARS raw response: {raw_text}")
            return _parse_action(raw_text)

        except requests.exceptions.Timeout:
            last_error = TimeoutError(f"API timed out after {API_TIMEOUT_SECONDS}s")
        except requests.exceptions.HTTPError as e:
            last_error = e
            # 4xx errors are not retryable (bad key, bad model, etc.)
            if response.status_code < 500:
                print(f"[brain] API error {response.status_code}: {response.text}")
                return None
        except (KeyError, IndexError) as e:
            last_error = e
            print(f"[brain] Unexpected API response structure: {e}")
            return None
        except Exception as e:
            last_error = e

        if attempt < MAX_RETRIES:
            wait = 2 ** attempt  # Exponential back-off
            print(f"[brain] Attempt {attempt} failed ({last_error}). Retrying in {wait}s…")
            time.sleep(wait)

    print(f"[brain] All {MAX_RETRIES} attempts failed. Last error: {last_error}")
    return None


# ── Action parser ──────────────────────────────────────────────────────────

# Allowed action types — no shell, no eval, no exec
ALLOWED_ACTIONS = {
    'click', 'double_click', 'right_click', 'type', 'key',
    'hotkey', 'scroll', 'move', 'open_app', 'switch_window',
    'wait', 'done', 'fail',
}


def _parse_action(raw: str) -> Optional[dict]:
    """
    Parse a raw UI-TARS action string into a structured dict.

    Examples:
        click(point='[50, 30]')         → {'type': 'click', 'point': [50, 30]}
        type(text='hello world')        → {'type': 'type', 'text': 'hello world'}
        done(message='Task complete')   → {'type': 'done', 'message': 'Task complete'}
    """
    # Strip markdown code fences if the model wraps the output
    raw = re.sub(r'```[a-z]*\n?', '', raw).strip('`').strip()

    # Match: action_name(key='value', ...)
    m = re.match(r'^(\w+)\((.*)\)$', raw, re.DOTALL)
    if not m:
        print(f"[brain] Cannot parse action: {raw!r}")
        return None

    action_type = m.group(1).lower()
    if action_type not in ALLOWED_ACTIONS:
        print(f"[brain] Rejected unknown action type: {action_type!r}")
        return None

    args_str = m.group(2)
    result: dict = {"type": action_type, "_raw": raw}

    # Extract point=[x, y] or point='[x, y]' (our requested format)
    point_m = re.search(r"point=['\"]?\[([0-9.]+),\s*([0-9.]+)\]['\"]?", args_str)
    if point_m:
        result["point"] = [float(point_m.group(1)), float(point_m.group(2))]
    else:
        # Fallback for UI-TARS native format: start_box='<|box_start|>(825,707)<|box_end|>'
        # These are scaled to 1000. So 825 = 82.5%
        box_m = re.search(r"\(([0-9]+),([0-9]+)\)", args_str)
        if box_m:
            x_val = float(box_m.group(1)) / 10.0
            y_val = float(box_m.group(2)) / 10.0
            result["point"] = [x_val, y_val]

    # Extract text='...'
    text_m = re.search(r"text=['\"](.+?)['\"](?=\s*[,)]|$)", args_str, re.DOTALL)
    if text_m:
        result["text"] = text_m.group(1)

    # Extract key='...'
    key_m = re.search(r"key=['\"](.+?)['\"]", args_str)
    if key_m:
        result["key"] = key_m.group(1)

    # Extract keys='...' (hotkey)
    keys_m = re.search(r"keys=['\"](.+?)['\"]", args_str)
    if keys_m:
        result["keys"] = keys_m.group(1)

    # Extract direction='...'
    dir_m = re.search(r"direction=['\"](\w+)['\"]", args_str)
    if dir_m:
        result["direction"] = dir_m.group(1)

    # Extract amount=N
    amt_m = re.search(r"amount=([0-9.]+)", args_str)
    if amt_m:
        result["amount"] = float(amt_m.group(1))

    # Extract seconds=N (wait)
    sec_m = re.search(r"seconds=([0-9.]+)", args_str)
    if sec_m:
        result["seconds"] = float(sec_m.group(1))

    # Extract message/reason/name
    for field in ("message", "reason", "name"):
        fld_m = re.search(rf"{field}=['\"](.+?)['\"]", args_str, re.DOTALL)
        if fld_m:
            result[field] = fld_m.group(1)

    return result
