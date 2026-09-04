# -*- coding: utf-8 -*-
"""Phase 1 smoke test -- validates all imports and core functions."""
import sys

errors = []

def ok(msg):
    print("  [PASS] " + str(msg))

def fail(msg, e):
    print("  [FAIL] " + str(msg) + ": " + str(e))
    errors.append(msg)


# 1. Config
try:
    from config.settings import FEATHERLESS_MODEL, MAX_ACTIONS_PER_TASK, get_api_key
    ok("config module loaded - Model: " + FEATHERLESS_MODEL)
except Exception as e:
    fail("config import", e)

# 2. Screen capture
try:
    from screen.capture import capture_screen, get_screen_size
    w, h = get_screen_size()
    ok("screen size: {}x{}".format(w, h))
    b64 = capture_screen()
    ok("screenshot captured: {} chars base64".format(len(b64)))
except Exception as e:
    fail("screen capture", e)

# 3. Validator
try:
    from computer.validator import validate_action
    valid, msg = validate_action({"type": "click", "point": [50.0, 50.0]})
    assert valid, "Expected valid click, got: " + str(msg)
    ok("validator: click(50,50) accepted")

    valid2, msg2 = validate_action({"type": "click", "point": [150.0, 50.0]})
    assert not valid2, "Expected out-of-range to be rejected"
    ok("validator: click(150,50) rejected -- " + msg2)

    valid3, msg3 = validate_action({"type": "key", "key": "enter"})
    assert valid3, "Expected 'enter' to be valid"
    ok("validator: key(enter) accepted")
except Exception as e:
    fail("validator", e)

# 4. Executor import
try:
    from computer.executor import execute_action, APP_ALLOW_LIST
    ok("executor imported -- {} apps in allow-list".format(len(APP_ALLOW_LIST)))
except Exception as e:
    fail("executor import", e)

# 5. Safety guard
try:
    from safety.guard import check_action, is_sensitive
    safe, _ = check_action({"type": "click", "point": [50, 50]})
    assert safe
    ok("guard: click is safe")

    blocked, reason = check_action({"type": "eval"})
    assert not blocked
    ok("guard: eval hard-blocked")

    sens, sreason = is_sensitive({"type": "type", "text": "click submit button"})
    assert sens
    ok("guard: 'submit' flagged as sensitive")
except Exception as e:
    fail("safety guard", e)

# 6. Brain parser
try:
    from agent.brain import _parse_action

    a1 = _parse_action("click(point='[25.5, 60.0]')")
    assert a1 and a1["type"] == "click" and a1["point"] == [25.5, 60.0], "Got: " + str(a1)
    ok("brain parser: click parsed correctly -- " + str(a1))

    a2 = _parse_action("type(text='hello world')")
    assert a2 and a2["type"] == "type" and a2["text"] == "hello world", "Got: " + str(a2)
    ok("brain parser: type parsed correctly")

    a3 = _parse_action("done(message='Task complete')")
    assert a3 and a3["type"] == "done"
    ok("brain parser: done parsed correctly")

    a4 = _parse_action("scroll(point='[50, 50]', direction='down', amount=3)")
    assert a4 and a4["direction"] == "down"
    ok("brain parser: scroll parsed correctly")

    a5 = _parse_action("totally_invalid_garbage!!!")
    assert a5 is None
    ok("brain parser: malformed input returns None")
except Exception as e:
    fail("brain parser", e)


# -- Summary ---------------------------------------------------------------
print()
if errors:
    print("[RESULT] {} test(s) FAILED: {}".format(len(errors), errors))
    sys.exit(1)
else:
    print("[RESULT] All Phase 1 smoke tests PASSED. Ready to run main.py")
