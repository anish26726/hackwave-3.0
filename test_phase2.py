# Phase 2 smoke test
import sys

errors = []

def ok(msg):
    print("  [PASS] " + str(msg))

def fail(msg, e):
    print("  [FAIL] " + str(msg) + ": " + str(e))
    errors.append(msg)

# 1. Brain parser - switch_window
try:
    from agent.brain import _parse_action, ALLOWED_ACTIONS
    assert "switch_window" in ALLOWED_ACTIONS
    a = _parse_action("switch_window(name='Calculator')")
    assert a and a["type"] == "switch_window" and a["name"] == "Calculator", str(a)
    ok("brain: switch_window parsed correctly -- " + str(a))

    d = _parse_action("done(message='Calculator opened.')")
    assert d and d["type"] == "done"
    ok("brain: done() still parses correctly")
except Exception as e:
    fail("brain parser", e)

# 2. Validator - switch_window
try:
    from computer.validator import validate_action
    ok2, msg2 = validate_action({"type": "switch_window", "name": "Calculator"})
    assert ok2, "Expected switch_window to be valid: " + msg2
    ok("validator: switch_window accepted")

    ok3, msg3 = validate_action({"type": "switch_window", "name": ""})
    assert not ok3
    ok("validator: empty switch_window rejected")
except Exception as e:
    fail("validator switch_window", e)

# 3. Executor - switch_window import
try:
    from computer.executor import _switch_window, APP_ALLOW_LIST
    ok("executor: _switch_window imported, {} apps in allow-list".format(len(APP_ALLOW_LIST)))
except Exception as e:
    fail("executor switch_window import", e)

# 4. Main loop settings
try:
    from main import MAX_CONSECUTIVE_REPEATS, ACTION_DELAYS
    assert MAX_CONSECUTIVE_REPEATS == 2
    ok("main: anti-repeat guard = {}".format(MAX_CONSECUTIVE_REPEATS))
    assert ACTION_DELAYS["open_app"] == 2.5
    ok("main: open_app delay = {}s".format(ACTION_DELAYS["open_app"]))
except Exception as e:
    fail("main settings", e)

# 5. All action types now supported
try:
    from agent.brain import ALLOWED_ACTIONS
    required = {"click", "double_click", "right_click", "type", "key",
                "hotkey", "scroll", "move", "open_app", "switch_window",
                "wait", "done", "fail"}
    missing = required - ALLOWED_ACTIONS
    assert not missing, "Missing from ALLOWED_ACTIONS: " + str(missing)
    ok("brain: all required action types present -- " + str(sorted(ALLOWED_ACTIONS)))
except Exception as e:
    fail("allowed actions", e)

print()
if errors:
    print("[RESULT] {} FAILED: {}".format(len(errors), errors))
    sys.exit(1)
else:
    print("[RESULT] All Phase 2 checks PASSED.")
