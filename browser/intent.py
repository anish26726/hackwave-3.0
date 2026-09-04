# AccessOS — Browser Intent Parser (Phase 6)
# Maps natural language browser commands to structured intent dicts.
# Chrome is the default browser.

import re
from typing import Optional
from urllib.parse import quote_plus

# ── Supported browsers ────────────────────────────────────────────────────
BROWSERS = {
    'chrome':  'chrome',
    'google':  'chrome',
    'firefox': 'firefox',
    'ff':      'firefox',
    'edge':    'edge',
    'msedge':  'edge',
}

# ── Trigger pattern ───────────────────────────────────────────────────────
_BROWSER_TRIGGER = re.compile(
    r'\b('
    r'open\s+(chrome|firefox|edge|browser|google chrome|microsoft edge)|'
    r'go\s+to\b|navigate\s+to\b|visit\b|browse\s+to\b|'
    r'search\s+(for|on|the\s+web\s+for)?\b|'
    r'google\s+(for|it|that|this)?\b|'
    r'go\s+back\b|back\b|previous\s+page\b|'
    r'go\s+forward\b|forward\b|next\s+page\b|'
    r'refresh\b|reload\b|'
    r'open\s+(a\s+)?(new\s+)?(tab|window)\b|'
    r'close\s+(the\s+)?(tab|browser)\b|'
    r'read\s+(the\s+)?(webpage|page|website|site|article)\b|'
    r'what.s\s+on\s+this\s+(page|site|website)\b'
    r')',
    re.IGNORECASE,
)


def is_browser_command(text: str) -> bool:
    """
    Return True if *text* is a browser/web operation.

    Deliberately avoids mis-routing generic 'open chrome' style computer-use
    commands that UI-TARS already handles — only fast deterministic browser
    ops are captured here.
    """
    return bool(_BROWSER_TRIGGER.search(text))


def parse_browser_intent(text: str) -> Optional[dict]:
    """
    Parse a natural language browser command into a structured intent dict.

    Returns:
        dict with 'op' key and op-specific fields, or None (caller falls back
        to UI-TARS).

    Supported ops:
        open_browser, navigate, search, back, forward, refresh,
        new_tab, close_tab, read_page
    """
    t = text.strip()
    tl = t.lower()

    # ── Detect which browser was mentioned ────────────────────────────────
    browser = _extract_browser(tl)

    # ── Read webpage ──────────────────────────────────────────────────────
    if re.search(
        r'\bread\s+(the\s+)?(page|webpage|website|site|article|this)\b'
        r'|\bwhat.?s\s+on\s+this\s+(page|site|website|screen)\b',
        tl
    ):
        return {'op': 'read_page'}

    # ── Go back ───────────────────────────────────────────────────────────
    if re.search(r'\b(go\s+back|previous\s+page|back)\b', tl):
        return {'op': 'back'}

    # ── Go forward ────────────────────────────────────────────────────────
    if re.search(r'\b(go\s+forward|forward|next\s+page)\b', tl):
        return {'op': 'forward'}

    # ── Refresh / Reload ──────────────────────────────────────────────────
    if re.search(r'\b(refresh|reload)\b', tl):
        return {'op': 'refresh'}

    # ── New tab ───────────────────────────────────────────────────────────
    if re.search(r'\bnew\s+tab\b', tl):
        return {'op': 'new_tab'}

    # ── Close tab ─────────────────────────────────────────────────────────
    if re.search(r'\bclose\s+(tab|this\s+tab)\b', tl):
        return {'op': 'close_tab'}

    # ── Navigate to URL ───────────────────────────────────────────────────
    # "go to youtube.com", "navigate to https://github.com", "visit reddit.com"
    m = re.search(
        r'\b(?:go\s+to|navigate\s+to|visit|browse\s+to|open)\s+'
        r'(?:the\s+)?(?:website\s+)?'
        r'["\']?(?:https?://)?([a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}(?:/[^\s"\']*)?)["\']?',
        t, re.IGNORECASE,
    )
    if m:
        raw_url = m.group(1).strip()
        url = _normalise_url(raw_url)
        return {'op': 'navigate', 'url': url, 'browser': browser}

    # ── Site-specific search: "search X in/on youtube" ──────────────────
    # Must run BEFORE the generic search pattern so it takes priority.
    # Patterns: "search X on youtube", "search X in youtube",
    #           "look up X on reddit", "find X on amazon"
    m = re.search(
        r'\b(?:search|look\s+up|find)\s+'
        r'["\']?(.+?)["\']?'
        r'\s+(?:in|on)\s+'
        r'(youtube|google|bing|reddit|amazon|wikipedia|twitter|x\.com|duck(?:duck)?go)\b',
        t, re.IGNORECASE,
    )
    if m:
        query  = m.group(1).strip().strip('"\'') 
        site   = m.group(2).strip().lower().replace('.com', '')
        # Normalise aliases
        site_map = {'x': 'twitter', 'duckduckgo': 'duck', 'duckgo': 'duck'}
        engine = site_map.get(site, site)
        return {'op': 'search', 'query': query, 'engine': engine, 'browser': browser}

    # ── Generic search: "search for X", "google for X", "look up X" ──────
    # Also handles: "open chrome and search for X"
    m = re.search(
        r'\b(?:search\s+(?:for|on\s+(?:google|the\s+web)\s+for)?|'
        r'google\s+(?:for\s+)?|'
        r'look\s+up\b)\s+'
        r'["\']?(.+?)["\']?$',
        t, re.IGNORECASE,
    )
    if m:
        query = m.group(1).strip().strip('"\'') 
        return {'op': 'search', 'query': query, 'engine': 'google', 'browser': browser}

    # ── Open browser only ─────────────────────────────────────────────────
    # "open chrome", "launch firefox"
    if re.search(r'\b(?:open|launch|start)\s+(?:the\s+)?(?:browser|chrome|firefox|edge|google chrome|microsoft edge)\b', tl):
        return {'op': 'open_browser', 'browser': browser}

    return None  # Falls back to UI-TARS


# ── Helpers ───────────────────────────────────────────────────────────────

def _extract_browser(text_lower: str) -> str:
    """Return the browser name mentioned in the text, default 'chrome'."""
    for keyword, name in BROWSERS.items():
        if re.search(rf'\b{re.escape(keyword)}\b', text_lower):
            return name
    return 'chrome'  # Default


def _normalise_url(raw: str) -> str:
    """Ensure URL has a valid scheme. Defaults to https://."""
    if re.match(r'^https?://', raw, re.IGNORECASE):
        return raw
    return f'https://{raw}'


def build_search_url(query: str, engine: str = 'google') -> str:
    """Build a search URL for the given query and engine/site."""
    encoded = quote_plus(query)
    engines = {
        'google':    f'https://www.google.com/search?q={encoded}',
        'bing':      f'https://www.bing.com/search?q={encoded}',
        'duck':      f'https://duckduckgo.com/?q={encoded}',
        'youtube':   f'https://www.youtube.com/results?search_query={encoded}',
        'reddit':    f'https://www.reddit.com/search/?q={encoded}',
        'amazon':    f'https://www.amazon.com/s?k={encoded}',
        'wikipedia': f'https://en.wikipedia.org/w/index.php?search={encoded}',
        'twitter':   f'https://twitter.com/search?q={encoded}',
    }
    return engines.get(engine, engines['google'])
