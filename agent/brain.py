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
7. If the task is already visibly complete from a PREVIOUS action, call done().
8. BROWSER RULE: When asked to open a browser or Chrome specifically, ALWAYS use open_app(name='Chrome'). Never open Edge or Firefox unless the user explicitly asks for them."""


def _build_messages(task: str, screenshot_b64: str, history: list[dict],
                    session_summary: str = "") -> list[dict]:
    """
    Build the messages array for the chat completions API.

    Only the CURRENT screenshot is sent as an image; previous steps are
    summarised as text to avoid payload/token explosion on long tasks.
    An optional session_summary injects cross-task context (L3 fix).
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Inject cross-task session context so follow-up commands work (L3 fix)
    if session_summary:
        messages.append({
            "role": "user",
            "content": f"Context from previous tasks:\n{session_summary}"
        })
        messages.append({
            "role": "assistant",
            "content": "Understood. I have context from previous actions."
        })

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


# ── Shared HTTP helper (B8 fix — eliminates copy-paste retry logic) ────────

def _featherless_post(
    messages: list,
    max_tokens: int,
    temperature: float,
) -> Optional[str]:
    """
    POST to the Featherless chat completions endpoint with retry + back-off.

    Args:
        messages:    List of chat message dicts.
        max_tokens:  Token budget for the response.
        temperature: Sampling temperature (0.0 = deterministic).

    Returns:
        Raw content string from the model, or None on failure.
    """
    url = f"{FEATHERLESS_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": FEATHERLESS_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=API_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()

        except requests.exceptions.Timeout:
            last_error = TimeoutError(f"API timed out after {API_TIMEOUT_SECONDS}s")
        except requests.exceptions.HTTPError as e:
            last_error = e
            # 429 Too Many Requests is retryable with back-off.
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


def ask_uitars(
    task: str,
    screenshot_b64: str,
    history: list[dict] | None = None,
    session_summary: str = "",
) -> Optional[dict]:
    """
    Send the current screen and task to UI-TARS via Featherless.

    Args:
        task:            The user's natural-language goal.
        screenshot_b64:  Base64-encoded JPEG of the current screen.
        history:         List of previous {action, observation} dicts for this task.
        session_summary: Optional cross-task context string (L3 fix).

    Returns:
        Parsed action dict, e.g. {"type": "click", "point": [640, 400]}
        Returns None on unrecoverable failure.
    """
    history = history or []
    messages = _build_messages(task, screenshot_b64, history, session_summary)
    raw_text = _featherless_post(messages, max_tokens=256, temperature=0.0)
    if raw_text is None:
        return None
    print(f"[brain] UI-TARS raw response: {raw_text}")
    return _parse_action(raw_text)


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
    desc = _featherless_post(messages, max_tokens=400, temperature=0.2)
    if desc:
        print(f"[brain] Screen description: {desc[:100]}...")
    else:
        print("[brain] describe_screen failed after all retries.")
    return desc


# ── Action parser ──────────────────────────────────────────────────────────

# Allowed action types — no shell, no eval, no exec
ALLOWED_ACTIONS = {
    'click', 'double_click', 'right_click', 'type', 'key',
    'hotkey', 'scroll', 'move', 'open_app', 'switch_window',
    'wait', 'done', 'fail',
}


def _pixels_to_pct(x_px: float, y_px: float) -> tuple:
    """
    Convert absolute pixel coordinates to 0-100% percentages.
    Uses pyautogui.size() for the current screen resolution.
    Falls back to 1920x1080 if pyautogui is unavailable.
    """
    try:
        import pyautogui as _pag
        sw, sh = _pag.size()
    except Exception:
        sw, sh = 1920, 1080   # Safe fallback for most laptops
    x_pct = round((x_px / sw) * 100, 2)
    y_pct = round((y_px / sh) * 100, 2)
    # Clamp to valid range
    x_pct = max(0.0, min(100.0, x_pct))
    y_pct = max(0.0, min(100.0, y_pct))
    return x_pct, y_pct


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
            # 2-coordinate form: (x, y).
            # UI-TARS uses 0-1000 scale, but occasionally outputs actual pixel
            # coordinates (e.g. on high-DPI displays or certain screen sizes).
            # Detect which scale is being used and convert accordingly.
            box2_m = re.search(r"\(([0-9]+),([0-9]+)\)", args_str)
            if box2_m:
                x_raw = float(box2_m.group(1))
                y_raw = float(box2_m.group(2))
                # Try 0-1000 scale first (divide by 10 → 0-100%)
                x_pct = x_raw / 10.0
                y_pct = y_raw / 10.0
                if x_pct > 100 or y_pct > 100:
                    # Values exceed 100% → they are pixel coordinates.
                    # Convert to percentage using actual screen size.
                    x_pct, y_pct = _pixels_to_pct(x_raw, y_raw)
                result["point"] = [round(x_pct, 2), round(y_pct, 2)]

    # Extract text='...'
    text_m = re.search(r"text=['\"](.+?)['\"](?=\s*[,)]|$)", args_str, re.DOTALL)
    if text_m:
        result["text"] = text_m.group(1)

    # Extract key='...'
    key_m = re.search(r"key=['\"](.+?)['\"]\s*(?=[,)]|$)", args_str)
    if key_m:
        result["key"] = key_m.group(1)

    # Extract keys='...' (hotkey)
    keys_m = re.search(r"keys=['\"](.+?)['\"]\s*(?=[,)]|$)", args_str)
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
        fld_m = re.search(rf"{field}=['\"](.+?)['\"]\s*(?=[,)]|$)", args_str, re.DOTALL)
        if fld_m:
            result[field] = fld_m.group(1)

    return result
