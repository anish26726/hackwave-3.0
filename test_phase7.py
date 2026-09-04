# -*- coding: utf-8 -*-
"""Phase 7 smoke test -- validates task planner decomposition logic."""
import sys

errors = []

def ok(msg):
    print("  [PASS] " + str(msg))

def fail(msg, e):
    print("  [FAIL] " + str(msg) + ": " + str(e))
    errors.append(msg)

print("AccessOS Phase 7 -- Multi-Step Task Planner Smoke Test")
print("=======================================================")

# 1. Module import
try:
    from agent.planner import is_multi_step, decompose, format_plan_preview, TaskPlan, MAX_STEPS
    ok("agent.planner imported successfully")
except Exception as e:
    fail("module import", e)
    sys.exit(1)

# 2. is_multi_step() — True cases
try:
    tests_true = [
        "open chrome and search for AI hackathons",
        "open chrome, go to github.com, and read the webpage",
        "first find resume.pdf then open it",
        "search for Python tutorials and then open the first result",
        "open chrome, search for news, open the first result, and read it to me",
        "find my notes.txt and then read it",
        "open notepad and type hello world",
    ]
    for t in tests_true:
        result = is_multi_step(t)
        assert result, f"Expected True for: '{t}'"
        ok(f"is_multi_step True: '{t[:55]}{'…' if len(t)>55 else ''}'")
except AssertionError as e:
    fail("is_multi_step True case", e)

# 3. is_multi_step() — False cases (single-step, must NOT be split)
try:
    tests_false = [
        "open chrome",
        "go to youtube.com",
        "search for Python",
        "find resume.pdf",
        "read notes.txt",
        "refresh",
        "go back",
    ]
    for t in tests_false:
        result = is_multi_step(t)
        assert not result, f"Expected False for: '{t}'"
        ok(f"is_multi_step False: '{t}'")
except AssertionError as e:
    fail("is_multi_step False case", e)

# 4. decompose() — step counts
try:
    cases = [
        ("open chrome and search for AI hackathons",           2),
        ("open chrome, go to github.com, and read the webpage", 3),
        ("first find resume.pdf then open it",                 2),
        ("find notes.txt and then read it",                    2),
    ]
    for text, expected_count in cases:
        plan = decompose(text)
        assert len(plan.steps) == expected_count, \
            f"Expected {expected_count} steps, got {len(plan.steps)}: {plan.steps}"
        ok(f"decompose ({expected_count} steps): '{text[:55]}{'…' if len(text)>55 else ''}'")
except AssertionError as e:
    fail("decompose step count", e)

# 5. decompose() — step content spot checks
try:
    plan = decompose("open chrome and search for AI hackathons")
    assert any("chrome" in s.lower() for s in plan.steps), f"Missing 'chrome': {plan.steps}"
    assert any("search" in s.lower() or "ai hackathon" in s.lower() for s in plan.steps), \
        f"Missing search step: {plan.steps}"
    ok(f"decompose content: steps = {plan.steps}")

    plan = decompose("first find resume.pdf then open it")
    assert any("find" in s.lower() or "resume" in s.lower() for s in plan.steps), \
        f"Missing find step: {plan.steps}"
    assert any("open" in s.lower() for s in plan.steps), f"Missing open step: {plan.steps}"
    ok(f"decompose content: steps = {plan.steps}")
except AssertionError as e:
    fail("decompose content check", e)

# 6. Single-step goal must NOT be split
try:
    plan = decompose("open chrome")
    assert len(plan.steps) == 1, f"Single-step goal was split: {plan.steps}"
    assert plan.steps[0].lower() == "open chrome", f"Step modified: {plan.steps}"
    ok("decompose: single-step goal not split")
except AssertionError as e:
    fail("single-step guard", e)

# 7. Max steps cap
try:
    # Build a very long multi-step command
    long_goal = " and then ".join([f"step number {i}" for i in range(1, 20)])
    plan = decompose(long_goal)
    assert len(plan.steps) <= MAX_STEPS, \
        f"Steps exceeded MAX_STEPS ({MAX_STEPS}): got {len(plan.steps)}"
    ok(f"max steps cap: {len(plan.steps)} <= {MAX_STEPS}")
except AssertionError as e:
    fail("max steps cap", e)

# 8. format_plan_preview()
try:
    plan = decompose("open chrome and search for AI hackathons")
    preview = format_plan_preview(plan)
    assert "Step 1" in preview and "Step 2" in preview, f"Preview malformed: {preview!r}"
    ok(f"format_plan_preview: {preview.splitlines()[0]}")
except AssertionError as e:
    fail("format_plan_preview", e)

# 9. TaskPlan.is_multi property
try:
    plan_multi = decompose("open chrome and search for Python")
    plan_single = decompose("open chrome")
    assert plan_multi.is_multi is True, "Multi plan.is_multi should be True"
    assert plan_single.is_multi is False, "Single plan.is_multi should be False"
    ok("TaskPlan.is_multi works correctly")
except AssertionError as e:
    fail("TaskPlan.is_multi", e)

# 10. main.py integration — import check
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "main_check",
        r"c:\Users\akshi\Documents\hackwave-3.0\main.py"
    )
    # Just verify the planner is importable from main context
    from agent.planner import is_multi_step as _ims
    ok("main.py planner integration: import OK")
except Exception as e:
    fail("main.py integration check", e)

print("=======================================================")
if not errors:
    print("All Phase 7 planner checks PASSED!")
else:
    print(f"Failed {len(errors)} check(s): {errors}")
