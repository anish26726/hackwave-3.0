# -*- coding: utf-8 -*-
"""Phase 6 smoke test -- validates browser intent parsing, URL validation, and module imports."""
import sys

errors = []

def ok(msg):
    print("  [PASS] " + str(msg))

def fail(msg, e):
    print("  [FAIL] " + str(msg) + ": " + str(e))
    errors.append(msg)

print("AccessOS Phase 6 -- Browser Automation Smoke Test")
print("==================================================")

# 1. Module Imports
try:
    from browser.handler import BrowserHandler, BrowserError, get_handler
    from browser.intent import is_browser_command, parse_browser_intent, build_search_url
    from browser.dom_reader import DOMReader, get_dom_reader
    ok("browser modules imported successfully")
except Exception as e:
    fail("module imports", e)
    sys.exit(1)

# 2. Intent Parsing — is_browser_command()
try:
    assert is_browser_command("open chrome"), "open chrome"
    ok("is_browser_command: 'open chrome' → True")

    assert is_browser_command("search for Python tutorials"), "search for"
    ok("is_browser_command: 'search for Python tutorials' → True")

    assert is_browser_command("go to youtube.com"), "go to"
    ok("is_browser_command: 'go to youtube.com' → True")

    assert is_browser_command("go back"), "go back"
    ok("is_browser_command: 'go back' → True")

    assert is_browser_command("refresh"), "refresh"
    ok("is_browser_command: 'refresh' → True")

    assert is_browser_command("read the webpage"), "read webpage"
    ok("is_browser_command: 'read the webpage' → True")

    # Should NOT be browser commands
    assert not is_browser_command("open notepad"), f"'open notepad' should be False"
    ok("is_browser_command: 'open notepad' → False (correct)")
except Exception as e:
    fail("is_browser_command detection", e)

# 3. Intent Parsing — parse_browser_intent()
try:
    # open browser
    intent = parse_browser_intent("open chrome")
    assert intent and intent["op"] == "open_browser" and intent["browser"] == "chrome", f"Got {intent}"
    ok("intent: 'open chrome' → op='open_browser', browser='chrome'")

    # navigate
    intent = parse_browser_intent("go to youtube.com")
    assert intent and intent["op"] == "navigate" and "youtube.com" in intent["url"], f"Got {intent}"
    ok(f"intent: 'go to youtube.com' → op='navigate', url={intent['url']}")

    # search
    intent = parse_browser_intent("search for Python tutorials")
    assert intent and intent["op"] == "search" and "Python tutorials" in intent["query"], f"Got {intent}"
    ok(f"intent: 'search for Python tutorials' → op='search', query={intent['query']}")

    # back
    intent = parse_browser_intent("go back")
    assert intent and intent["op"] == "back", f"Got {intent}"
    ok("intent: 'go back' → op='back'")

    # forward
    intent = parse_browser_intent("go forward")
    assert intent and intent["op"] == "forward", f"Got {intent}"
    ok("intent: 'go forward' → op='forward'")

    # refresh
    intent = parse_browser_intent("refresh")
    assert intent and intent["op"] == "refresh", f"Got {intent}"
    ok("intent: 'refresh' → op='refresh'")

    # new tab
    intent = parse_browser_intent("open a new tab")
    assert intent and intent["op"] == "new_tab", f"Got {intent}"
    ok("intent: 'open a new tab' → op='new_tab'")

    # read page
    intent = parse_browser_intent("read the webpage")
    assert intent and intent["op"] == "read_page", f"Got {intent}"
    ok("intent: 'read the webpage' → op='read_page'")

    # Compound: open chrome and search
    intent = parse_browser_intent("search for AI hackathons on google")
    assert intent and intent["op"] == "search" and "AI hackathons" in intent["query"], f"Got {intent}"
    ok(f"intent: 'search for AI hackathons on google' → op='search', query={intent['query']}")
except Exception as e:
    fail("parse_browser_intent", e)

# 4. URL Validation & normalisation
try:
    handler = get_handler()

    # Valid https
    url = handler._validate_url("https://www.google.com")
    assert url == "https://www.google.com", f"Got {url}"
    ok("URL validation: https://www.google.com accepted")

    # Auto-adds https://
    url = handler._validate_url("youtube.com")
    assert url == "https://youtube.com", f"Got {url}"
    ok("URL normalise: 'youtube.com' → 'https://youtube.com'")

    # Blocks javascript:
    try:
        handler._validate_url("javascript:alert('xss')")
        fail("URL security", "Expected BrowserError for javascript: scheme")
    except BrowserError:
        ok("URL security: 'javascript:' scheme blocked")

    # Blocks data:
    try:
        handler._validate_url("data:text/html,<h1>test</h1>")
        fail("URL security", "Expected BrowserError for data: scheme")
    except BrowserError:
        ok("URL security: 'data:' scheme blocked")
except Exception as e:
    fail("URL validation", e)

# 5. Build search URL
try:
    url = build_search_url("Python tutorials", "google")
    assert "google.com/search" in url and "Python" in url, f"Got {url}"
    ok(f"build_search_url: Google search URL built correctly")

    url = build_search_url("AI hackathons", "bing")
    assert "bing.com/search" in url and "AI" in url, f"Got {url}"
    ok(f"build_search_url: Bing search URL built correctly")
except Exception as e:
    fail("build_search_url", e)

# 6. DOM Reader availability check (non-destructive)
try:
    reader = get_dom_reader()
    available = reader.is_available()
    if available:
        ok("DOMReader: Chrome CDP is active and reachable")
        tab = reader.get_active_tab()
        if tab:
            ok(f"DOMReader: Active tab found — '{tab.get('title', 'Unknown')}'")
        else:
            ok("DOMReader: CDP reachable but no active tabs found (Chrome may be empty)")
    else:
        ok("DOMReader: Chrome CDP not active (Chrome not running with --remote-debugging-port) — this is OK")
except Exception as e:
    fail("DOMReader availability check", e)

print("==================================================")
if not errors:
    print("All Phase 6 browser automation checks PASSED!")
else:
    print(f"Failed {len(errors)} check(s): {errors}")
