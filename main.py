# AccessOS -- Main Entry Point (Phase 4)
# Core loop:
#   Text input  -> Screenshot -> UI-TARS -> Validate -> Safety Check
#   -> Execute  -> Verify -> Anti-repetition -> Repeat
#
# Voice mode (Phase 3):
#   Wake word -> STT -> run_task() -> TTS response
# Screen reader (Phase 4):
#   Read command -> Screenshot -> OCR + UI-TARS -> Organised text -> TTS
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

# -- Phase 3: Voice modules (graceful degradation if pyaudio missing) ---------
try:
    from voice.tts import TTS
    from voice.stt import STT
    from voice.wake_word import WakeWordDetector
    _VOICE_AVAILABLE = True
except Exception as _ve:
    _VOICE_AVAILABLE = False
    print(f"[main] Voice modules unavailable: {_ve}")

# -- Phase 4: Screen reader ---------------------------------------------------
try:
    from screen.reader import get_reader, is_screen_read_command
    from PIL import Image
    import io, base64
    _READER_AVAILABLE = True
except Exception as _re:
    _READER_AVAILABLE = False
    print(f"[main] Screen reader unavailable: {_re}")

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

        # -- Step 2b: First-action guard -------------------------------------
        # Prevent the model from calling done()/fail() on the very first step
        # without having attempted anything. This stops hallucination where the
        # model claims a task is already complete without taking any action.
        if action_count == 0 and action["type"] in ("done", "fail"):
            print("[loop] WARNING: Model called {}() without attempting the task. "
                  "Forcing a real action.".format(action["type"]))
            history.append({
                "action": action.get("_raw", "done()"),
                "result": (
                    "REJECTED: You must attempt the task before calling done() or fail(). "
                    "No actions have been taken yet. Please perform the task now."
                ),
                "observation": screenshot
            })
            action_count += 1
            continue

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


# -- Screen reader (Phase 4) --------------------------------------------------

def run_screen_reader(task: str, tts=None) -> str:
    """
    Capture the current screen and return an organised text description.
    Speaks the result aloud if a TTS instance is provided.

    Pipeline:
        Screenshot → OCR + Win32 accessibility → UI-TARS vision → TTS

    Args:
        task: The user's read/describe request.
        tts:  Optional TTS instance for voice output.

    Returns:
        Human-readable screen description string.
    """
    if not _READER_AVAILABLE:
        msg = "Screen reader is not available."
        if tts:
            tts.speak(msg)
        return msg

    print("[reader] Capturing screen for reading...")
    try:
        screenshot_b64 = capture_screen()
    except RuntimeError as e:
        msg = f"Screenshot failed: {e}"
        if tts:
            tts.speak(msg)
        return msg

    # Decode b64 → PIL Image for OCR
    try:
        raw_bytes = base64.b64decode(screenshot_b64)
        screenshot_pil = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception as e:
        screenshot_pil = None
        print(f"[reader] Could not decode screenshot for OCR: {e}")

    reader = get_reader()
    description = reader.read(
        screenshot_pil=screenshot_pil,
        screenshot_b64=screenshot_b64,
        user_query=task,
    )

    print(f"\n[reader] Screen content:\n{description}\n")

    if tts:
        # Offline Windows TTS (pyttsx3) handles long text natively.
        # Calling speak() in a loop breaks the engine loop, so we pass it whole.
        tts.speak(description)

    return description


# -- Voice mode ---------------------------------------------------------------

def run_voice_mode() -> None:
    """
    Full voice pipeline:
        Wake word → STT → run_task() → TTS

    The loop runs indefinitely until the user says 'stop' / 'quit' / 'exit',
    presses Ctrl+C, or moves the mouse to the top-left corner.
    """
    if not _VOICE_AVAILABLE:
        print("[Voice] Voice mode is not available.")
        print("[Voice] Install pyaudio: python -m pip install pyaudio")
        return

    tts = TTS()
    stt = STT(listen_timeout=8.0, phrase_time_limit=12.0)
    detector = WakeWordDetector()

    if not stt.available:
        print("[Voice] Microphone/PyAudio not available. "
              "Cannot start voice mode.")
        return

    tts.speak("Access OS voice mode active. Say Hey Access to give a command.")
    stt.calibrate()

    print("\n[Voice] Listening for 'Hey Access'... (Ctrl+C to stop)")

    try:
        while True:
            # Step 1 — Wait for wake word
            detected = detector.wait_for_wake_word()
            if not detected:
                break

            tts.speak("Yes?")

            # Step 2 — Listen for the command
            print("[Voice] Listening for command...")
            command = stt.listen_once(prompt="Speak your command now...")

            if not command:
                tts.speak("Sorry, I didn't catch that. Say Hey Access to try again.")
                continue

            print(f"[Voice] Command: {command!r}")

            # Step 3 — Check for stop commands
            if any(w in command.lower() for w in ("stop", "quit", "exit", "goodbye")):
                tts.speak("Goodbye. Stopping voice mode.")
                break

            # Step 4 — Confirm command back to user
            tts.speak_async(f"Running: {command}")

            # Step 5 — Route to screen reader or action loop
            try:
                # Screen reading commands → reader (Phase 4)
                if _READER_AVAILABLE and is_screen_read_command(command):
                    run_screen_reader(command, tts=tts)
                else:
                    # Action commands → UI-TARS executor loop
                    result = run_task(command)
                    print(f"[Voice] Result: {result}")
                    spoken = result if len(result) <= 120 else result[:120] + "."
                    tts.speak(spoken)
            except pyautogui.FailSafeException:
                tts.speak("Emergency stop triggered.")
                break
            except Exception as e:
                tts.speak(f"An error occurred: {e}")
                print(f"[Voice] Error: {e}")

    except KeyboardInterrupt:
        print("\n[Voice] Voice mode stopped by user.")
        tts.speak("Voice mode stopped.")


# -- CLI entry point -----------------------------------------------------------

def main():
    # Check for --voice flag
    voice_mode = "--voice" in sys.argv

    print("=" * 60)
    print("  AccessOS -- AI Computer-Use Agent (Phase 4)")
    print("  Model: UI-TARS-1.5-7B via Featherless")
    print("  Emergency Stop: move mouse to top-left corner")
    if voice_mode:
        print("  Mode: VOICE (wake word: 'Hey Access')")
    else:
        print("  Mode: TEXT  (type 'voice' to switch, 'read' to read screen)")
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

    # Launch voice mode directly if --voice flag passed
    if voice_mode:
        run_voice_mode()
        return

    # Text mode (default)
    while True:
        try:
            task = input("Enter task (or 'quit' / 'voice'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[AccessOS] Exiting.")
            break

        if not task:
            continue

        if task.lower() in ("quit", "exit", "q"):
            print("[AccessOS] Goodbye.")
            break

        # Switch to voice mode on demand
        if task.lower() == "voice":
            run_voice_mode()
            continue

        # Route screen-reading commands to the screen reader (Phase 4)
        if _READER_AVAILABLE and is_screen_read_command(task):
            tts_instance = TTS() if _VOICE_AVAILABLE else None
            result = run_screen_reader(task, tts=tts_instance)
            print("\n[AccessOS] Screen read complete.\n")
            continue

        result = run_task(task)
        print("\n[AccessOS] Final result: {}\n".format(result))


if __name__ == "__main__":
    main()
