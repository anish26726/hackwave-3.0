# AccessOS -- Main Entry Point (Phase 6)
# Core loop:
#   Text input  -> Screenshot -> UI-TARS -> Validate -> Safety Check
#   -> Execute  -> Verify -> Anti-repetition -> Repeat
#
# Voice mode (Phase 3):
#   Wake word -> STT -> run_task() -> TTS response
# Screen reader (Phase 4):
#   Read command -> Screenshot -> OCR + UI-TARS -> Organised text -> TTS
# File operations (Phase 5):
#   File command -> Intent parser -> FileHandler -> Safety check -> Execute -> TTS
# Browser automation (Phase 6):
#   Browser command -> Intent parser -> BrowserHandler -> Execute -> DOM read -> TTS

import sys
import time
import pyautogui

from config.settings import MAX_ACTIONS_PER_TASK, SESSION_CONTEXT_SIZE
from screen.capture import capture_screen
from agent.brain import ask_uitars
from computer.validator import validate_action
from computer.executor import execute_action
from safety.guard import check_action, is_sensitive

# -- Phase 3: Voice modules ---------------------------------------------------
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

# -- Phase 5: File operations -------------------------------------------------
try:
    from files.handler import get_handler, FileOperationError
    from files.intent import is_file_command, parse_file_intent, KNOWN_DIRS
    from pathlib import Path
    _FILES_AVAILABLE = True
except Exception as _fe:
    _FILES_AVAILABLE = False
    print(f"[main] File module unavailable: {_fe}")

# -- Phase 6: Browser automation -----------------------------------------------
try:
    from browser.handler import get_handler as get_browser_handler, BrowserError
    from browser.intent import is_browser_command, parse_browser_intent
    _BROWSER_AVAILABLE = True
except Exception as _be:
    _BROWSER_AVAILABLE = False
    print(f"[main] Browser module unavailable: {_be}")


def _refocus_terminal() -> None:
    """
    Return keyboard focus to the AccessOS terminal window after a browser
    operation. Without this, keystrokes (and voice-command results) go to
    Chrome's address bar instead of the AccessOS prompt.

    Strategy:
      1. Try pygetwindow — match by common terminal title substrings.
      2. Fallback: Alt+Tab back to the previous window.
    """
    try:
        import pygetwindow as _gw
        # Window title fragments that indicate a terminal running AccessOS
        TERMINAL_HINTS = [
            'python', 'accessos', 'powershell', 'windows terminal',
            'cmd', 'command prompt', 'terminal',
        ]
        all_windows = _gw.getAllWindows()
        for hint in TERMINAL_HINTS:
            matches = [
                w for w in all_windows
                if hint in w.title.lower() and w.title.strip()
            ]
            if matches:
                try:
                    matches[0].activate()
                except Exception:
                    matches[0].minimize()
                    import time as _t; _t.sleep(0.15)
                    matches[0].restore()
                import time as _t; _t.sleep(0.4)
                return
    except Exception:
        pass

    # Last-resort fallback — Alt+Tab back
    try:
        import pyautogui as _pg
        import time as _t
        _pg.hotkey('alt', 'tab')
        _t.sleep(0.4)
    except Exception:
        pass

# -- Module-level TTS singleton -----------------------------------------------
_tts: "TTS | None" = None


def _get_tts() -> "TTS | None":
    """Return the module-level TTS singleton, creating it once if needed."""
    global _tts
    if _tts is None and _VOICE_AVAILABLE:
        _tts = TTS()
    return _tts


# -- Cross-task session context -----------------------------------------------

class SessionContext:
    """
    Lightweight cross-task memory so follow-up commands work.
    Also tracks the last file path operated on for "this file" references.
    """

    def __init__(self, max_entries: int = SESSION_CONTEXT_SIZE):
        self._entries: list[dict] = []
        self._max = max_entries
        self.last_file_path: "str | None" = None   # Phase 5: last file context

    def add(self, task: str, result: str) -> None:
        self._entries.append({"task": task, "result": result[:200]})
        if len(self._entries) > self._max:
            self._entries.pop(0)

    def summary(self) -> str:
        if not self._entries:
            return ""
        lines = [
            f"  [{i + 1}] Task: {e['task']!r} → {e['result']}"
            for i, e in enumerate(self._entries[-3:])
        ]
        ctx = "Recent task history (for context only):\n" + "\n".join(lines)
        if self.last_file_path:
            ctx += f"\nLast file operated on: {self.last_file_path}"
        return ctx

    def clear(self) -> None:
        self._entries.clear()
        self.last_file_path = None

    def __len__(self) -> int:
        return len(self._entries)


_session = SessionContext()

# -- Emergency stop -----------------------------------------------------------
STOP_REQUESTED = False


def request_stop():
    global STOP_REQUESTED
    STOP_REQUESTED = True


# -- Per-action delays --------------------------------------------------------
ACTION_DELAYS = {
    "open_app":      2.5,
    "switch_window": 1.5,
    "key":           0.3,
    "hotkey":        0.4,
    "click":         0.5,
    "double_click":  0.5,
    "right_click":   0.5,
    "type":          0.3,
    "scroll":        0.3,
    "move":          0.2,
    "wait":          0.0,
    "done":          0.0,
    "fail":          0.0,
}
DEFAULT_DELAY = 0.6
MAX_CONSECUTIVE_REPEATS = 2


def _get_delay(action_type: str) -> float:
    return ACTION_DELAYS.get(action_type, DEFAULT_DELAY)


# ── Phase 2/3/4 — UI-TARS computer-use loop ──────────────────────────────

def run_task(task: str) -> str:
    """
    Run one complete task through the UI-TARS → executor loop.
    """
    global STOP_REQUESTED
    STOP_REQUESTED = False

    print("\n[AccessOS] Task: " + task)
    print("[AccessOS] Starting agent loop...")

    session_summary = _session.summary()
    if session_summary:
        print(f"[loop] Session context: {len(_session)} recent tasks available.")

    history: list[dict] = []
    action_count = 0
    consecutive_repeats = 0
    last_raw_action = None

    while action_count < MAX_ACTIONS_PER_TASK:
        if STOP_REQUESTED:
            result = "Task cancelled by user."
            _session.add(task, result)
            return result

        print("\n[loop] Action {}/{} -- capturing screen...".format(
            action_count + 1, MAX_ACTIONS_PER_TASK))
        try:
            screenshot = capture_screen()
        except RuntimeError as e:
            result = "Screenshot failed: {}".format(e)
            _session.add(task, result)
            return result

        print("[loop] Sending to UI-TARS via Featherless...")
        action = ask_uitars(task, screenshot, history, session_summary=session_summary)

        if action is None:
            result = "Agent failed: UI-TARS did not return a valid action."
            _session.add(task, result)
            return result

        print("[loop] Action received: {}".format(action))

        # First-action guard — prevent done()/fail() before any action
        if action_count == 0 and action["type"] in ("done", "fail"):
            print("[loop] WARNING: Model called {}() without attempting the task.".format(
                action["type"]))
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

        # Anti-repetition guard
        current_raw = action.get("_raw", "")
        if current_raw == last_raw_action:
            consecutive_repeats += 1
            if consecutive_repeats >= MAX_CONSECUTIVE_REPEATS:
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

        # Safety check
        safe, reason = check_action(action)
        if not safe:
            result = "Action blocked by safety layer: {}".format(reason)
            _session.add(task, result)
            return result

        sensitive, sens_reason = is_sensitive(action)
        if sensitive:
            print("\nSENSITIVE ACTION DETECTED: {}".format(sens_reason))
            try:
                confirm = input("Allow this action? (yes/no): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "no"
            if confirm not in ("yes", "y"):
                result = "User declined sensitive action. Task stopped."
                _session.add(task, result)
                return result

        # Validate
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

        # Terminal actions
        if action["type"] == "done":
            msg = action.get("message", "Task completed.")
            print("\n[OK] " + msg)
            _session.add(task, msg)
            return msg

        if action["type"] == "fail":
            msg = action.get("reason", "Task failed.")
            print("\n[FAIL] " + msg)
            _session.add(task, f"FAILED: {msg}")
            return msg

        # Execute
        print("[loop] Executing: {}...".format(action["type"]))
        try:
            result = execute_action(action)
            print("[loop] Result: " + result)
        except pyautogui.FailSafeException:
            final = "Emergency stop triggered (mouse moved to corner)."
            _session.add(task, final)
            return final
        except Exception as e:
            result = "Execution error: {}".format(e)
            print("[loop] " + result)

        delay = _get_delay(action["type"])
        if delay > 0:
            time.sleep(delay)

        try:
            new_screenshot = capture_screen()
        except RuntimeError:
            new_screenshot = screenshot

        history.append({
            "action": action.get("_raw", ""),
            "result": result,
            "observation": new_screenshot
        })
        action_count += 1

    final = "Reached maximum action limit ({}). Task stopped.".format(MAX_ACTIONS_PER_TASK)
    _session.add(task, final)
    return final


# ── Phase 4 — Screen reader ───────────────────────────────────────────────

def run_screen_reader(task: str, tts=None) -> str:
    """Capture current screen and return/speak an organised description."""
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

    screenshot_pil = None
    try:
        raw_bytes = base64.b64decode(screenshot_b64)
        screenshot_pil = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception as e:
        print(f"[reader] Could not decode screenshot for OCR: {e}")

    reader = get_reader()
    description = reader.read(
        screenshot_pil=screenshot_pil,
        screenshot_b64=screenshot_b64,
        user_query=task,
    )

    print(f"\n[reader] Screen content:\n{description}\n")
    if tts:
        tts.speak(description)
    return description


# ── Phase 5 — File operations ─────────────────────────────────────────────

def run_file_command(task: str, tts=None) -> str:
    """
    Handle a file or document operation command.

    Pipeline:
        Natural language → intent parser → FileHandler → safety check → execute → TTS

    Args:
        task: User's natural language file command.
        tts:  Optional TTS for voice output.

    Returns:
        Result string.
    """
    if not _FILES_AVAILABLE:
        msg = "File operations are not available."
        if tts:
            tts.speak(msg)
        return msg

    intent = parse_file_intent(task)

    if intent is None:
        # Cannot parse — fall back to the UI-TARS loop which can handle
        # more complex or visual file interactions (e.g. clicking a file in Explorer)
        print("[files] Intent unclear — falling back to UI-TARS loop.")
        return run_task(task)

    handler = get_handler()
    op = intent['op']
    print(f"[files] Operation: {op} | Intent: {intent}")

    try:
        # ── find ──────────────────────────────────────────────────────────
        if op == 'find':
            results = handler.find_file(
                pattern=intent['pattern'],
                search_dir=intent.get('search_dir'),
                max_age_days=intent.get('max_age_days'),
            )
            if not results:
                msg = f"No files found matching '{intent['pattern']}'."
            else:
                # Store first result for follow-up commands ("open it", "read it")
                _session.last_file_path = results[0]
                if len(results) == 1:
                    msg = f"Found 1 file:\n  {results[0]}"
                else:
                    listing = '\n'.join(
                        f"  {i + 1}. {r}" for i, r in enumerate(results[:5])
                    )
                    msg = f"Found {len(results)} files:\n{listing}"
                    if len(results) > 5:
                        msg += f"\n  … and {len(results) - 5} more."
                _session.add(task, f"Found {len(results)} files matching '{intent['pattern']}'")

        # ── open ──────────────────────────────────────────────────────────
        elif op == 'open':
            path = intent.get('path')
            if not path:
                path = _resolve_path_from_intent(intent, handler)
                if isinstance(path, str) and path.startswith("ERROR:"):
                    msg = path[6:]
                    if tts:
                        tts.speak(msg)
                    return msg
            result = handler.open_file(path)
            _session.last_file_path = path
            _session.add(task, result)
            msg = result

        # ── create_folder ─────────────────────────────────────────────────
        elif op == 'create_folder':
            path = intent.get('path')
            if not path:
                name = intent.get('name', 'New Folder')
                path = str(Path.home() / 'Desktop' / name)
            result = handler.create_folder(path)
            _session.add(task, result)
            msg = result

        # ── rename ────────────────────────────────────────────────────────
        elif op == 'rename':
            path = intent.get('path') or _session.last_file_path
            new_name = intent.get('new_name', '')
            if not path:
                msg = "Please specify which file to rename, or use 'find' to locate it first."
            elif not new_name:
                msg = "Please specify the new name. Example: 'rename this file to report_final.txt'"
            else:
                result = handler.rename_file(path, new_name)
                _session.add(task, result)
                _session.last_file_path = str(Path(path).parent / new_name)
                msg = result

        # ── move ──────────────────────────────────────────────────────────
        elif op == 'move':
            path = intent.get('path') or _resolve_path_from_intent(intent, handler)
            dst = intent.get('dst')
            if not path or (isinstance(path, str) and path.startswith("ERROR:")):
                msg = (path[6:] if isinstance(path, str) and path.startswith("ERROR:")
                       else "Please specify which file to move.")
            elif not dst:
                msg = ("Please specify the destination folder. "
                       "Example: 'move report.pdf to Documents'")
            else:
                result = handler.move_file(path, dst)
                _session.add(task, result)
                msg = result

        # ── copy ──────────────────────────────────────────────────────────
        elif op == 'copy':
            path = intent.get('path') or _resolve_path_from_intent(intent, handler)
            dst = intent.get('dst')
            if not path or (isinstance(path, str) and path.startswith("ERROR:")):
                msg = (path[6:] if isinstance(path, str) and path.startswith("ERROR:")
                       else "Please specify which file to copy.")
            elif not dst:
                msg = ("Please specify the destination folder. "
                       "Example: 'copy report.pdf to Desktop'")
            else:
                result = handler.copy_file(path, dst)
                _session.add(task, result)
                msg = result

        # ── delete ────────────────────────────────────────────────────────
        elif op == 'delete':
            path = intent.get('path') or _resolve_path_from_intent(intent, handler)
            if not path or (isinstance(path, str) and path.startswith("ERROR:")):
                msg = (path[6:] if isinstance(path, str) and path.startswith("ERROR:")
                       else "Please specify which file to delete.")
            else:
                fname = Path(path).name
                print(f"\n⚠️  DELETE REQUESTED: {path}")
                print(f"  This will PERMANENTLY delete '{fname}'.")
                try:
                    confirm = input(f"  Confirm delete '{fname}'? (yes/no): ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    confirm = "no"
                if confirm not in ("yes", "y"):
                    msg = f"Delete cancelled — '{fname}' was not deleted."
                else:
                    result = handler.delete_file(path)
                    if _session.last_file_path == path:
                        _session.last_file_path = None
                    _session.add(task, result)
                    msg = result

        # ── read_doc ──────────────────────────────────────────────────────
        elif op == 'read_doc':
            path = intent.get('path') or _resolve_path_from_intent(intent, handler)
            if not path or (isinstance(path, str) and path.startswith("ERROR:")):
                msg = (path[6:] if isinstance(path, str) and path.startswith("ERROR:")
                       else "Please specify which document to read.")
            else:
                text = handler.read_document(path)
                _session.last_file_path = path
                _session.add(task, f"Read document: {Path(path).name}")
                msg = text

        # ── read_pdf ──────────────────────────────────────────────────────
        elif op == 'read_pdf':
            path = intent.get('path') or _resolve_path_from_intent(intent, handler)
            page = intent.get('page', 1)
            if not path or (isinstance(path, str) and path.startswith("ERROR:")):
                msg = (path[6:] if isinstance(path, str) and path.startswith("ERROR:")
                       else "Please specify which PDF to read.")
            else:
                text = handler.read_pdf_page(path, page=page)
                _session.last_file_path = path
                _session.add(task, f"Read PDF page {page}: {Path(path).name}")
                msg = text

        else:
            msg = f"Unsupported file operation: {op}"

    except FileOperationError as e:
        msg = f"File operation failed: {e}"
    except Exception as e:
        msg = f"Unexpected error during file operation: {e}"
        print(f"[files] Exception: {e}")

    print(f"\n[files] Result: {msg[:200]}\n")
    if tts:
        # Speak a concise version (TTS handles long text but trim for comfort)
        spoken = msg if len(msg) <= 300 else msg[:300] + "."
        tts.speak(spoken)

    return msg


# ── Phase 6 — Browser automation ─────────────────────────────────────────

def run_browser_command(task: str, tts=None) -> str:
    """
    Handle a browser or web navigation command.

    Pipeline:
        Natural language → intent parser → BrowserHandler → execute → DOM read → TTS

    Args:
        task: User's natural language browser command.
        tts:  Optional TTS for voice output.

    Returns:
        Result string.
    """
    if not _BROWSER_AVAILABLE:
        msg = "Browser module is not available."
        if tts:
            tts.speak(msg)
        return msg

    intent = parse_browser_intent(task)

    if intent is None:
        print("[browser] Intent unclear — falling back to UI-TARS loop.")
        return run_task(task)

    handler = get_browser_handler()
    op = intent.get('op')
    browser = intent.get('browser', 'chrome')
    print(f"[browser] Operation: {op} | Intent: {intent}")

    try:
        # ── open browser ──────────────────────────────────────────────────
        if op == 'open_browser':
            msg = handler.open_browser(browser=browser)

        # ── navigate to URL ───────────────────────────────────────────────
        elif op == 'navigate':
            url = intent.get('url', '')
            if not url:
                msg = "Please specify a URL. Example: 'go to youtube.com'"
            else:
                msg = handler.navigate(url, browser=browser)

        # ── search ────────────────────────────────────────────────────────
        elif op == 'search':
            query = intent.get('query', '')
            if not query:
                msg = "Please specify a search query. Example: 'search for Python tutorials'"
            else:
                msg = handler.search(query, browser=browser)

        # ── back ──────────────────────────────────────────────────────────
        elif op == 'back':
            msg = handler.go_back(browser=browser)

        # ── forward ───────────────────────────────────────────────────────
        elif op == 'forward':
            msg = handler.go_forward(browser=browser)

        # ── refresh ───────────────────────────────────────────────────────
        elif op == 'refresh':
            msg = handler.refresh(browser=browser)

        # ── new tab ───────────────────────────────────────────────────────
        elif op == 'new_tab':
            msg = handler.new_tab(browser=browser)

        # ── close tab ─────────────────────────────────────────────────────
        elif op == 'close_tab':
            msg = handler.close_tab(browser=browser)

        # ── read page via DOM reader ───────────────────────────────────────
        elif op == 'read_page':
            print("[browser] Reading current webpage via Chrome CDP...")
            msg = handler.read_page(query=task)

        else:
            msg = f"Unsupported browser operation: {op}"

    except BrowserError as e:
        msg = f"Browser operation failed: {e}"
    except Exception as e:
        msg = f"Unexpected browser error: {e}"
        print(f"[browser] Exception: {e}")

    print(f"\n[browser] Result: {msg[:300]}\n")
    if tts:
        spoken = msg if len(msg) <= 300 else msg[:300] + "."
        tts.speak(spoken)

    # Return focus to terminal so the next voice/text command goes here,
    # not to Chrome's address bar.
    _refocus_terminal()

    return msg


def _resolve_path_from_intent(intent: dict, handler) -> str:
    """
    Try to resolve a file path from intent context:
      1. Use session's last_file_path if intent has no filename
      2. Direct check if path exists in CWD, Desktop, Documents, Downloads
      3. Search by filename via handler.find_file()
      4. Return an "ERROR:..." string if resolution fails

    Returns the resolved path string, or "ERROR:<message>".
    """
    # Use last_file_path from session (e.g. user said "open it" after a find)
    filename = intent.get('filename')
    if not filename and _session.last_file_path:
        return _session.last_file_path

    if not filename:
        return "ERROR:Please specify a file name or use 'find' first."

    # Direct check: does it exist directly as entered or relative to CWD?
    cand = Path(filename)
    if cand.exists():
        res = str(cand.resolve())
        _session.last_file_path = res
        return res

    # Check common user folders directly
    for base in (Path.cwd(), Path.home() / 'Desktop', Path.home() / 'Documents', Path.home() / 'Downloads'):
        direct = base / filename
        if direct.exists():
            res = str(direct.resolve())
            _session.last_file_path = res
            return res

    results = handler.find_file(filename)
    if not results:
        return f"ERROR:File '{filename}' was not found. Try 'find {filename}' first."

    # Return the most recent match
    _session.last_file_path = results[0]
    if len(results) > 1:
        print(f"[files] Multiple matches for '{filename}', using most recent: {results[0]}")
    return results[0]


# ── Voice mode ────────────────────────────────────────────────────────────

def run_voice_mode() -> None:
    """
    Full voice pipeline:
        Wake word → STT → route → TTS

    Supports: screen read (Phase 4), file operations (Phase 5),
              general computer control (Phase 2/3).
    """
    if not _VOICE_AVAILABLE:
        print("[Voice] Voice mode is not available.")
        print("[Voice] Install pyaudio: python -m pip install pyaudio")
        return

    tts = _get_tts()
    stt = STT(listen_timeout=8.0, phrase_time_limit=12.0)
    detector = WakeWordDetector()

    if not stt.available:
        print("[Voice] Microphone/PyAudio not available.")
        return

    tts.speak("Access OS voice mode active. Say Hey Access to give a command.")
    stt.calibrate()
    print("\n[Voice] Listening for 'Hey Access'... (Ctrl+C to stop)")

    try:
        while True:
            detected = detector.wait_for_wake_word()
            if not detected:
                break

            tts.speak("Yes?")
            print("[Voice] Listening for command...")
            command = stt.listen_once(prompt="Speak your command now...")

            if not command:
                tts.speak("Sorry, I didn't catch that. Say Hey Access to try again.")
                continue

            print(f"[Voice] Command: {command!r}")

            if any(w in command.lower() for w in ("stop", "quit", "exit", "goodbye")):
                tts.speak("Goodbye. Stopping voice mode.")
                break

            tts.speak_async(f"Running: {command}")

            try:
                if _READER_AVAILABLE and is_screen_read_command(command):
                    run_screen_reader(command, tts=tts)
                elif _FILES_AVAILABLE and is_file_command(command):
                    run_file_command(command, tts=tts)
                else:
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


# ── CLI entry point ───────────────────────────────────────────────────────

def main():
    voice_mode = "--voice" in sys.argv

    print("=" * 60)
    print("  AccessOS -- AI Computer-Use Agent (Phase 5)")
    print("  Model: UI-TARS-1.5-7B via Featherless")
    print("  Emergency Stop: move mouse to top-left corner")
    if voice_mode:
        print("  Mode: VOICE (wake word: 'Hey Access')")
    else:
        print("  Mode: TEXT  (type 'voice' to switch, 'read' to read screen)")
        print("  Ctrl+C during a task cancels it without exiting")
        print("  Type 'reset' to clear session context")
    print("  Type 'quit' or 'exit' to close.")
    print("=" * 60)

    try:
        from config.settings import get_api_key, FEATHERLESS_MODEL
        get_api_key()
        print(f"  API: Featherless | Model: {FEATHERLESS_MODEL}")
        print("  API key: OK")
    except EnvironmentError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

    # Report available modules
    modules = []
    if _VOICE_AVAILABLE:
        modules.append("Voice")
    if _READER_AVAILABLE:
        modules.append("Screen Reader")
    if _FILES_AVAILABLE:
        modules.append("File Operations")
    print(f"  Active modules: {', '.join(modules) if modules else 'Core only'}")
    print()

    if voice_mode:
        run_voice_mode()
        return

    # Text mode
    while True:
        try:
            task = input("Enter task (or 'quit' / 'voice' / 'reset'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[AccessOS] Exiting.")
            break

        if not task:
            continue

        if task.lower() in ("quit", "exit", "q"):
            print("[AccessOS] Goodbye.")
            break

        if task.lower() == "voice":
            run_voice_mode()
            continue

        if task.lower() in ("reset", "clear context", "new session"):
            _session.clear()
            print("[AccessOS] Session context cleared.\n")
            continue

        # ── Routing ──────────────────────────────────────────────────────
        # 1. Screen reader commands (Phase 4)
        if _READER_AVAILABLE and is_screen_read_command(task):
            tts_instance = _get_tts()
            run_screen_reader(task, tts=tts_instance)
            print("\n[AccessOS] Screen read complete.\n")
            continue

        # 2. File operation commands (Phase 5)
        if _FILES_AVAILABLE and is_file_command(task):
            tts_instance = _get_tts()
            try:
                run_file_command(task, tts=tts_instance)
            except KeyboardInterrupt:
                print("\n[AccessOS] File operation cancelled.\n")
            print("\n[AccessOS] File operation complete.\n")
            continue

        # 2.5 Browser commands (Phase 6)
        if _BROWSER_AVAILABLE and is_browser_command(task):
            tts_instance = _get_tts()
            try:
                run_browser_command(task, tts=tts_instance)
            except KeyboardInterrupt:
                print("\n[AccessOS] Browser operation cancelled.\n")
            print("\n[AccessOS] Browser operation complete.\n")
            continue

        # 3. General computer-use task → UI-TARS loop (Phase 2/3)
        try:
            result = run_task(task)
        except KeyboardInterrupt:
            request_stop()
            print("\n[AccessOS] Task cancelled by user (Ctrl+C).\n")
            continue

        print("\n[AccessOS] Final result: {}\n".format(result))


if __name__ == "__main__":
    main()
