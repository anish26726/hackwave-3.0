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
1. ALWAYS perform the required action first. NEVER call done() on the very first step without attempting the task.
2. Only call done() AFTER you have already executed an action AND you can clearly see the result on screen.
3. If the task is to open an app, use open_app() first — only call done() after the app window is visible on screen.
4. NEVER repeat the exact same action twice in a row. If an action did not work, try something different.
5. Do NOT write explanations, chain-of-thought, or extra text. ONE action line only.
6. If you just successfully opened an app and can see it on screen, call done().
7. If the task is already visibly complete from a PREVIOUS action, call done()."""


def _build_messages(task: str, screenshot_b64: str, history: list[dict]) -> list[dict]:
    """
    Build the messages array for the chat completions API.

    Only the CURRENT screenshot is sent as an image; previous steps are
    summarised as text to avoid payload/token explosion on long tasks.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Include action history as text-only (no old screenshots) so the
    # context stays small regardless of how many steps have run.
    for entry in history:
        action_text = entry.get("action", "")
        result_text = entry.get("result", "")
        assistant_content = action_text
        if result_text:
            assistant_content = f"{action_text}  # Result: {result_text}"
        messages.append({"role": "assistant", "content": assistant_content})

        # Send a text-only follow-up (no base64 blob) for historical steps.
        messages.append({
            "role": "user",
            "content": (
                f"After that action the screen updated (result: {result_text}). "
                f"Continue working on the task: {task}. "
                f"If the task is now complete, call done()."
            )
        })

    # Current turn — only this screenshot is sent as an image.
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
            # 429 Too Many Requests is retryable with backoff.
            # Other 4xx errors (bad key, bad model, etc.) are not retryable.
            if response.status_code != 429 and response.status_code < 500:
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


# ── Screen description (Phase 4) ───────────────────────────────────────────

READ_SCREEN_PROMPT = """You are AccessOS, a Windows screen reader for visually impaired users.
You receive a screenshot. Describe the visible content clearly and concisely so it can be read aloud.

RULES:
1. Identify the active application/window first.
2. Read the main content in logical order (top-to-bottom, left-to-right).
3. For web pages: title, main heading, main content, important links or buttons.
4. For documents/PDFs: heading, visible text, page number if shown.
5. For apps/dialogs: window title, labels, field values, buttons, any error messages.
6. If there is an error or warning dialog, read the EXACT error message.
7. Mention important interactive elements (buttons, checkboxes, links) but skip decorative icons.
8. Do NOT suggest actions. Just describe what is visible.
9. Keep the output under 200 words. Be concise and natural — it will be spoken aloud."""


def describe_screen(screenshot_b64: str, user_query: str = "Read the screen") -> Optional[str]:
    """
    Ask UI-TARS to describe the current screen for reading purposes.

    Uses READ_SCREEN_PROMPT (describe mode) instead of SYSTEM_PROMPT (action mode).

    Args:
        screenshot_b64: Base64-encoded JPEG of the current screen.
        user_query:     What the user asked (used to focus the description).

    Returns:
        Plain text description suitable for TTS, or None on failure.
    """
    url = f"{FEATHERLESS_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type": "application/json",
    }
    messages = [
        {"role": "system", "content": READ_SCREEN_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"User request: \"{user_query}\"\n"
                        "Please describe what is visible on this screen."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                },
            ],
        },
    ]
    payload = {
        "model": FEATHERLESS_MODEL,
        "messages": messages,
        "max_tokens": 400,
        "temperature": 0.2,
    }

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=API_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
            desc = data["choices"][0]["message"]["content"].strip()
            print(f"[brain] Screen description: {desc[:100]}...")
            return desc
        except requests.exceptions.Timeout:
            last_error = TimeoutError(f"API timed out after {API_TIMEOUT_SECONDS}s")
        except requests.exceptions.HTTPError as e:
            last_error = e
            if response.status_code != 429 and response.status_code < 500:
                print(f"[brain] describe_screen API error {response.status_code}")
                return None
        except Exception as e:
            last_error = e

        if attempt < MAX_RETRIES:
            wait = 2 ** attempt
            print(f"[brain] describe_screen attempt {attempt} failed. Retrying in {wait}s…")
            time.sleep(wait)

    print(f"[brain] describe_screen failed after {MAX_RETRIES} attempts: {last_error}")
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
        # Fallback for UI-TARS native 4-coordinate bounding box:
        #   start_box='<|box_start|>(ymin,xmin,ymax,xmax)<|box_end|>'
        #   OR simple 2-point form: (x, y) — all coordinates scaled 0-1000.
        # Try 4-coordinate form first (ymin, xmin, ymax, xmax).
        box4_m = re.search(
            r"\(([0-9]+),([0-9]+),([0-9]+),([0-9]+)\)", args_str
        )
        if box4_m:
            ymin = float(box4_m.group(1))
            xmin = float(box4_m.group(2))
            ymax = float(box4_m.group(3))
            xmax = float(box4_m.group(4))
            # Compute center and convert from 0-1000 scale to 0-100 percent.
            x_val = ((xmin + xmax) / 2.0) / 10.0
            y_val = ((ymin + ymax) / 2.0) / 10.0
            result["point"] = [round(x_val, 2), round(y_val, 2)]
        else:
            # 2-coordinate form: (x, y) scaled 0-1000 → 0-100%.
            box2_m = re.search(r"\(([0-9]+),([0-9]+)\)", args_str)
            if box2_m:
                x_val = float(box2_m.group(1)) / 10.0
                y_val = float(box2_m.group(2)) / 10.0
                result["point"] = [round(x_val, 2), round(y_val, 2)]

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
