# AccessOS — Vision Screen Summarizer (Phase 7+)
#
# Uses Qwen2-VL-7B-Instruct (vision-language model) via Featherless
# to describe and summarize what is visible on the screen.
#
# This is SEPARATE from UI-TARS (action model). Qwen2-VL is trained
# to DESCRIBE images in fluent natural language, making it ideal for:
#   - "What's on my screen?"
#   - "Read the webpage to me"
#   - "Summarize what you see"
#
# For webpages, we prefer CDP DOM extraction → Qwen2.5-7B (text)
# which is faster and more accurate than vision OCR.

import os
import time
import requests
from typing import Optional

from config.settings import get_api_key, FEATHERLESS_BASE_URL

# ── Model settings ─────────────────────────────────────────────────────────
SUMMARIZER_MODEL   = os.environ.get('SUMMARIZER_MODEL',   'Qwen/Qwen2-VL-7B-Instruct')
SUMMARIZER_TIMEOUT = int(os.environ.get('SUMMARIZER_TIMEOUT', '20'))  # seconds

# ── System prompts ─────────────────────────────────────────────────────────
_SCREEN_PROMPT = """\
You are AccessOS screen reader. Look at the screenshot and describe what you see clearly
and concisely for a user who cannot see the screen. Focus on:
- The main application or window that is open
- The key content visible (text, images, lists, forms)
- Any important notifications or dialogs
- The current state of the interface

Keep the description natural and conversational. Aim for 2-4 sentences.
Do NOT describe UI chrome (taskbar, window borders) unless relevant.
"""

_WEBPAGE_PROMPT = """\
You are AccessOS screen reader. The user wants to hear what's on the current webpage.
Summarize the main content clearly and concisely:
- State the page title and main topic
- Summarize the key information (2-4 sentences)
- Mention any important links or actions if relevant

Be natural and conversational. Do not list every element — focus on what matters.
"""


def summarize_screen(screenshot_b64: str, context: str = "") -> str:
    """
    Use Qwen2-VL to describe what is visible on screen.

    Args:
        screenshot_b64: Base64-encoded JPEG of the current screen.
        context:        Optional hint about what the user is asking
                        (e.g. "webpage", "desktop", "what's on screen").

    Returns:
        Human-readable description string, or error message on failure.
    """
    prompt = _WEBPAGE_PROMPT if "web" in context.lower() or "page" in context.lower() \
             else _SCREEN_PROMPT

    try:
        api_key = get_api_key()
    except EnvironmentError as e:
        return f"Cannot summarize: {e}"

    url     = f"{FEATHERLESS_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":       SUMMARIZER_MODEL,
        "messages":    [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": context if context else "What is currently on the screen?"
                    },
                    {
                        "type":      "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}
                    }
                ]
            }
        ],
        "max_tokens":  400,
        "temperature": 0.3,
    }

    try:
        t0   = time.time()
        resp = requests.post(url, headers=headers, json=payload, timeout=SUMMARIZER_TIMEOUT)
        resp.raise_for_status()
        summary     = resp.json()["choices"][0]["message"]["content"].strip()
        latency_ms  = int((time.time() - t0) * 1000)
        print(f"[summarizer] Described screen in {latency_ms}ms")
        return summary

    except requests.exceptions.Timeout:
        return f"Screen summarization timed out after {SUMMARIZER_TIMEOUT}s."
    except Exception as e:
        return f"Screen summarization failed: {e}"


def summarize_text(text: str, context: str = "") -> str:
    """
    Use Qwen2.5-7B (text-only, faster) to summarize extracted webpage text.
    Called when DOM text is already available — no screenshot needed.

    Args:
        text:    Raw webpage/document text.
        context: What the user asked (e.g. "read this page", "summarize").

    Returns:
        Human-readable summary string.
    """
    from agent.intent_model import INTENT_MODEL, INTENT_TIMEOUT

    if not text or not text.strip():
        return "The page appears to be empty or could not be read."

    # Truncate very long pages — ~4000 chars is plenty for a summary
    truncated = text[:4000]
    if len(text) > 4000:
        truncated += "\n[... content truncated ...]"

    system = (
        "You are AccessOS screen reader. Summarize the following webpage content "
        "clearly and concisely for the user. State the main topic and key points "
        "in 2-4 natural sentences. Be conversational."
    )
    user_msg = f"{context}\n\nPage content:\n{truncated}" if context else \
               f"Summarize this page:\n{truncated}"

    try:
        api_key = get_api_key()
    except EnvironmentError as e:
        return f"Cannot summarize: {e}"

    url     = f"{FEATHERLESS_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":       INTENT_MODEL,   # Reuse fast text model for text summarization
        "messages":    [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        "max_tokens":  300,
        "temperature": 0.3,
    }

    try:
        t0   = time.time()
        resp = requests.post(url, headers=headers, json=payload, timeout=INTENT_TIMEOUT)
        resp.raise_for_status()
        summary    = resp.json()["choices"][0]["message"]["content"].strip()
        latency_ms = int((time.time() - t0) * 1000)
        print(f"[summarizer] Text summarized in {latency_ms}ms")
        return summary
    except Exception as e:
        return f"Summarization failed: {e}"
