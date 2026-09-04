# AccessOS — LLM Intent Classifier (Phase 7+)
#
# Uses a fast text-only model (Qwen2.5-7B-Instruct) via Featherless API
# to classify natural-language commands into structured JSON intents.
#
# Replaces the regex-based intent parsers for:
#   - browser commands  (browser/intent.py)
#   - file commands     (files/intent.py)
#   - multi-step plans  (agent/planner.py)
#
# Falls back to regex parsers if the API is unavailable or times out.
# UI-TARS is reserved for "general" intents that require visual reasoning.

import json
import re
import time
import requests
from typing import Optional

from config.settings import get_api_key, FEATHERLESS_BASE_URL

# ── Model settings ─────────────────────────────────────────────────────────
import os
INTENT_MODEL   = os.environ.get('INTENT_MODEL',   'Qwen/Qwen2.5-7B-Instruct')
INTENT_TIMEOUT = int(os.environ.get('INTENT_MODEL_TIMEOUT', '8'))  # seconds

# ── System prompt ─────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are the intent classifier for AccessOS, an AI computer-use assistant on Windows.
Analyze the user command and return ONLY a valid JSON object. No explanation, no markdown.

## Output Schema
{
  "type": "browser" | "file" | "screen_read" | "multi_step" | "general",
  "op": string,
  "browser": "chrome",
  "url": string,
  "query": string,
  "engine": "google" | "youtube" | "bing" | "reddit" | "amazon" | "wikipedia" | "twitter",
  "filename": string,
  "path": string,
  "steps": [string]
}

## Browser ops
open_browser, navigate, search, back, forward, refresh, new_tab, close_tab, read_page

## File ops
find, open, create_folder, rename, move, copy, delete, read_doc, read_pdf

## Rules (examples)
"open chrome"                         → {"type":"browser","op":"open_browser","browser":"chrome"}
"go to youtube.com"                   → {"type":"browser","op":"navigate","url":"https://youtube.com","browser":"chrome"}
"search python on youtube"            → {"type":"browser","op":"search","query":"python","engine":"youtube","browser":"chrome"}
"search for AI hackathons"            → {"type":"browser","op":"search","query":"AI hackathons","engine":"google","browser":"chrome"}
"search pw in youtube"                → {"type":"browser","op":"search","query":"pw","engine":"youtube","browser":"chrome"}
"go back"                             → {"type":"browser","op":"back","browser":"chrome"}
"refresh the page"                    → {"type":"browser","op":"refresh","browser":"chrome"}
"read the webpage"                    → {"type":"browser","op":"read_page","browser":"chrome"}
"find resume.pdf"                     → {"type":"file","op":"find","filename":"resume.pdf"}
"read notes.txt"                      → {"type":"file","op":"read_doc","filename":"notes.txt"}
"open my project folder"              → {"type":"file","op":"find","filename":"project"}
"what's on my screen"                 → {"type":"screen_read","op":"read_screen"}
"read what you see"                   → {"type":"screen_read","op":"read_screen"}
"open chrome and search for AI"       → {"type":"multi_step","steps":["open chrome","search for AI"]}
"find notes.txt and read it"          → {"type":"multi_step","steps":["find notes.txt","read notes.txt"]}
"open chrome, go to github.com, read the page" → {"type":"multi_step","steps":["open chrome","go to github.com","read the webpage"]}
"click the submit button"             → {"type":"general"}
"type hello in the search box"        → {"type":"general"}
"open notepad"                        → {"type":"general"}

## Multi-step rule
If the command has 2+ clearly sequential actions (connected by "and", "then", "also",
"after that", commas, or "first...then"), return multi_step with steps split into
the smallest meaningful individual actions. Each step should be a complete command
that can be understood independently.

Only include JSON fields relevant to the type/op. Return valid JSON only.
"""


def _extract_json(raw: str) -> Optional[dict]:
    """
    Robustly extract a JSON object from the model response.
    Handles markdown code fences, leading/trailing text.
    """
    if not raw:
        return None

    # Strip markdown code fences
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip()

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Find first {...} block
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


def classify_intent(text: str) -> Optional[dict]:
    """
    Classify a natural-language command into a structured intent dict.

    Returns a dict with at minimum {"type": ...}, or None if the API
    call fails or times out (caller should fall back to regex parsers).

    Args:
        text: User's natural language command.

    Returns:
        Intent dict, e.g.:
            {"type": "browser", "op": "search", "query": "python", "engine": "youtube"}
            {"type": "multi_step", "steps": ["open chrome", "search for AI"]}
            {"type": "general"}
        Or None on failure.
    """
    try:
        api_key = get_api_key()
    except EnvironmentError:
        return None

    url     = f"{FEATHERLESS_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":       INTENT_MODEL,
        "messages":    [
            {"role": "system",  "content": _SYSTEM_PROMPT},
            {"role": "user",    "content": text},
        ],
        "max_tokens":  256,
        "temperature": 0.0,   # Deterministic — we want consistent JSON
    }

    try:
        t0 = time.time()
        resp = requests.post(url, headers=headers, json=payload, timeout=INTENT_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        latency_ms = int((time.time() - t0) * 1000)
        print(f"[intent] Classified in {latency_ms}ms: {raw[:120]}")
        result = _extract_json(raw)
        if result and "type" in result:
            return result
        print(f"[intent] Invalid JSON from model: {raw[:200]}")
        return None

    except requests.exceptions.Timeout:
        print(f"[intent] Model timed out after {INTENT_TIMEOUT}s — falling back to regex")
        return None
    except Exception as e:
        print(f"[intent] API error: {e} — falling back to regex")
        return None
