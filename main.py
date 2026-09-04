# AccessOS -- Main Entry Point (Phase 2)
# Core loop:
#   Text input -> Screenshot -> UI-TARS -> Validate -> Safety Check
#   -> Execute -> Verify -> Anti-repetition -> Repeat
#
# Voice and wake-word will be added in Phase 3.
# Safety confirmations will be enhanced in Phase 8.

import sys
import time
import pyautogui

from config.settings import MAX_ACTIONS_PER_TASK
from screen.capture import capture_screen
from agent.brain import ask_uitars
from computer.validator import validate_action
from computer.executor import execute_action
from safety.guard import check_action, is_sensitive

# -- Emergency stop --------------------------------------------------------
# Moving the mouse to the top-left corner triggers pyautogui.FailSafeException
# which is caught here and terminates the current task immediately.

STOP_REQUESTED = False


def request_stop():
    global STOP_REQUESTED
    STOP_REQUESTED = True


# -- Per-action delays (seconds) -------------------------------------------
# Actions that open apps or switch windows need longer settle time.
ACTION_DELAYS = {
    "open_app":      2.5,   # OS needs time to launch the window
    "switch_window": 1.5,
    "key":           0.3,
    "hotkey":        0.4,
    "click":         0.5,
    "double_click":  0.5,
    "right_click":   0.5,
    "type":          0.3,
    "scroll":        0.3,
    "move":          0.2,
    "wait":          0.0,   # wait already sleeps internally
    "done":          0.0,
    "fail":          0.0,
}
DEFAULT_DELAY = 0.6

# Max times the exact same raw action can appear consecutively before
# the loop aborts. Prevents infinite "open Calculator" loops.
MAX_CONSECUTIVE_REPEATS = 2


def _get_delay(action_type: str) -> float:
    return ACTION_DELAYS.get(action_type, DEFAULT_DELAY)


def run_task(task: str) -> str:
    """
    Run one complete task through the UI-TARS -> executor loop.

    Args:
        task: Natural-language goal from the user.

    Returns:
        Final status string.
    """
    global STOP_REQUESTED
    STOP_REQUESTED = False

    print("\n[AccessOS] Task: " + task)
    print("[AccessOS] Starting agent loop...")

    history: list[dict] = []
    action_count = 0
    consecutive_repeats = 0
    last_raw_action = None

    while action_count < MAX_ACTIONS_PER_TASK:
        if STOP_REQUESTED:
            return "Task cancelled by user."

        # -- Step 1: Capture current screen ----------------------------------
        print("\n[loop] Action {}/{} -- capturing screen...".format(
            action_count + 1, MAX_ACTIONS_PER_TASK))
        try:
            screenshot = capture_screen()
        except RuntimeError as e:
            return "Screenshot failed: {}".format(e)

        # -- Step 2: Ask UI-TARS for the next action -------------------------
        print("[loop] Sending to UI-TARS via Featherless...")
        action = ask_uitars(task, screenshot, history)

        if action is None:
            return "Agent failed: UI-TARS did not return a valid action."

        print("[loop] Action received: {}".format(action))

        # -- Step 3: Anti-repetition guard -----------------------------------
        current_raw = action.get("_raw", "")
        if current_raw == last_raw_action:
            consecutive_repeats += 1
            if consecutive_repeats >= MAX_CONSECUTIVE_REPEATS:
                # Force the agent to reconsider by injecting a hint
                # Add a fake "already done" entry to break the loop
                print("[loop] WARNING: Same action repeated {} times. Injecting done check.".format(
                    consecutive_repeats))
                history.append({
                    "action": current_raw,
                    "result": "ACTION REPEATED -- check if task is already complete and call done()",
                    "observation": screenshot
                })
                action_count += 1
                consecutive_repeats = 0
                last_raw_action = None
                continue
        else:
            consecutive_repeats = 0

        last_raw_action = current_raw

        # -- Step 4: Safety check --------------------------------------------
        safe, reason = check_action(action)
        if not safe:
            return "Action blocked by safety layer: {}".format(reason)

        sensitive, sens_reason = is_sensitive(action)
        if sensitive:
            print("\nSENSITIVE ACTION DETECTED: {}".format(sens_reason))
            try:
                confirm = input("Allow this action? (yes/no): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "no"
            if confirm not in ("yes", "y"):
                return "User declined sensitive action. Task stopped."

        # -- Step 5: Validate action -----------------------------------------
        valid, val_reason = validate_action(action)
        if not valid:
            print("[loop] Invalid action skipped: {}".format(val_reason))
            history.append({
                "action": action.get("_raw", "invalid"),
                "result": "INVALID: {}".format(val_reason),
                "observation": screenshot
            })
            action_count += 1
            continue

        # -- Step 6: Terminal actions ----------------------------------------
        if action["type"] == "done":
            msg = action.get("message", "Task completed.")
            print("\n[OK] " + msg)
            return msg

        if action["type"] == "fail":
            msg = action.get("reason", "Task failed.")
            print("\n[FAIL] " + msg)
            return msg

        # -- Step 7: Execute the action --------------------------------------
        print("[loop] Executing: {}...".format(action["type"]))
        try:
            result = execute_action(action)
            print("[loop] Result: " + result)
        except pyautogui.FailSafeException:
            return "Emergency stop triggered (mouse moved to corner)."
        except Exception as e:
            result = "Execution error: {}".format(e)
            print("[loop] " + result)

        # -- Step 8: Settle delay (action-type aware) ------------------------
        delay = _get_delay(action["type"])
        if delay > 0:
            time.sleep(delay)

        # -- Step 9: Capture updated screen and add to history ---------------
        try:
            new_screenshot = capture_screen()
        except RuntimeError:
            new_screenshot = screenshot  # Fallback to last known

        history.append({
            "action": action.get("_raw", ""),
            "result": result,
            "observation": new_screenshot
        })
        action_count += 1

    return "Reached maximum action limit ({}). Task stopped.".format(MAX_ACTIONS_PER_TASK)


# -- CLI entry point -------------------------------------------------------

def main():
    print("=" * 60)
    print("  AccessOS -- AI Computer-Use Agent (Phase 2)")
    print("  Model: UI-TARS-1.5-7B via Featherless")
    print("  Emergency Stop: move mouse to top-left corner")
    print("  Type 'quit' or 'exit' to close.")
    print("=" * 60)

    # Validate environment on startup
    try:
        from config.settings import get_api_key, FEATHERLESS_MODEL
        get_api_key()   # Will raise if key is missing or placeholder
        print("  API: Featherless | Model: {}".format(FEATHERLESS_MODEL))
        print("  API key: OK")
    except EnvironmentError as e:
        print("\n[ERROR] {}".format(e))
        sys.exit(1)

    print()

    while True:
        try:
            task = input("Enter task (or 'quit'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[AccessOS] Exiting.")
            break

        if not task:
            continue

        if task.lower() in ("quit", "exit", "q"):
            print("[AccessOS] Goodbye.")
            break

        result = run_task(task)
        print("\n[AccessOS] Final result: {}\n".format(result))


if __name__ == "__main__":
    main()
