# AccessOS -- Main Entry Point (Phase 8)
# Safety pipeline:
#   LLM intent → Content safety check → Route to handler
#   UI-TARS action → check_action → is_sensitive (confirm) → validate → execute
#   Screen/web text → check_content_safety → sanitize → model
#   File delete → confirm_sensitive_action (always required)

import sys
import time
import threading
import pyautogui

from config.settings import MAX_ACTIONS_PER_TASK, SESSION_CONTEXT_SIZE, TASK_TIMEOUT_SECONDS
from screen.capture import capture_screen
from agent.brain import ask_uitars
from computer.validator import validate_action
from computer.executor import execute_action
from safety.guard import (
    check_action, is_sensitive,
    check_content_safety, sanitize_content, confirm_sensitive_action,
)

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

# -- Phase 7: Multi-step task planner -----------------------------------------
try:
    from agent.planner import is_multi_step, decompose, format_plan_preview
    _PLANNER_AVAILABLE = True
except Exception as _pe:
    _PLANNER_AVAILABLE = False
    print(f"[main] Planner unavailable: {_pe}")

# -- Phase 7+: LLM intent classifier (Qwen2.5-7B) ----------------------------
try:
    from agent.intent_model import classify_intent
    _INTENT_LLM_AVAILABLE = True
except Exception as _ie:
    _INTENT_LLM_AVAILABLE = False
    print(f"[main] LLM intent model unavailable: {_ie}")

# -- Phase 7+: Vision screen summarizer (Qwen2-VL) ----------------------------
try:
    from agent.screen_summarizer import summarize_screen, summarize_text
    _SUMMARIZER_AVAILABLE = True
except Exception as _se:
    _SUMMARIZER_AVAILABLE = False
    print(f"[main] Vision summarizer unavailable: {_se}")

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
        self.last_file_path: "str | None" = None     # Phase 5: last file context
        self.last_browser_url: "str | None" = None   # Phase 7: last navigated URL
        self.last_step_results: list[tuple] = []     # Phase 7: [(step_text, result), …]

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
        if self.last_browser_url:
            ctx += f"\nLast browser URL: {self.last_browser_url}"
        if self.last_step_results:
            ctx += "\nLast multi-step results:"
            for step_text, step_result in self.last_step_results[-3:]:
                ctx += f"\n  - {step_text!r} → {step_result[:80]}"
        return ctx

    def clear(self) -> None:
        self._entries.clear()
        self.last_file_path = None
        self.last_browser_url = None
        self.last_step_results = []

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
    task_start_time = time.time()   # Phase 8: wall-clock timeout

    while action_count < MAX_ACTIONS_PER_TASK:
        if STOP_REQUESTED:
            result = "Task cancelled by user."
            _session.add(task, result)
            return result

        # Phase 8: wall-clock timeout guard
        elapsed = time.time() - task_start_time
        if elapsed > TASK_TIMEOUT_SECONDS:
            result = (
                f"Task timed out after {int(elapsed)}s "
                f"(limit: {TASK_TIMEOUT_SECONDS}s). "
                "Increase TASK_TIMEOUT_SECONDS in .env if needed."
            )
            print(f"[loop] ⏱ {result}")
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
            query  = intent.get('query', '')
            engine = intent.get('engine', 'google')   # site-specific or default
            if not query:
                msg = "Please specify a search query. Example: 'search for Python tutorials'"
            else:
                msg = handler.search(query, browser=browser, engine=engine)

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


# ── Phase 7 — Multi-step autonomous task execution ────────────────────────

def run_multi_step_task(goal: str, tts=None) -> str:
    """
    Decompose a compound goal into sub-steps and execute each one in order.

    Pipeline for each step:
        is_file_command?    → run_file_command()
        is_browser_command? → run_browser_command()
        else                → run_task() (UI-TARS loop)

    Safety:
        - Checks STOP_REQUESTED before every step.
        - 1 retry per step on failure.
        - Max steps capped at MAX_STEPS (10).
        - Ctrl+C cancels remaining steps cleanly.

    Args:
        goal: User's compound natural language goal.
        tts:  Optional TTS for voice output.

    Returns:
        Summary string of what was accomplished.
    """
    global STOP_REQUESTED

    if not _PLANNER_AVAILABLE:
        # Planner not loaded — fall back to single-step
        return run_task(goal)

    plan = decompose(goal)
    preview = format_plan_preview(plan)

    if preview:
        print(preview)
        if tts:
            tts.speak(f"Starting {len(plan.steps)}-step task.")

    step_results: list[tuple[str, str]] = []
    completed = 0
    failed_steps: list[str] = []

    for i, step in enumerate(plan.steps, 1):
        if STOP_REQUESTED:
            msg = f"Task cancelled after {completed}/{len(plan.steps)} steps."
            if tts:
                tts.speak(msg)
            break

        print(f"\n[plan] ── Step {i}/{len(plan.steps)}: {step!r} ──")

        step_result = _execute_single_step(step, tts=tts)

        # Check if step failed — retry once
        is_fail = (
            step_result.startswith("FAIL")
            or step_result.startswith("Agent failed")
            or step_result.startswith("Screenshot failed")
        )

        if is_fail:
            print(f"[plan] Step {i} failed: {step_result[:100]}. Retrying once...")
            import time as _t; _t.sleep(1.0)
            step_result = _execute_single_step(step, tts=None)  # No TTS on retry
            is_fail = (
                step_result.startswith("FAIL")
                or step_result.startswith("Agent failed")
            )

        if is_fail:
            print(f"[plan] ✗ Step {i} FAILED: {step_result[:100]}")
            failed_steps.append(step)
            step_results.append((step, f"FAILED: {step_result[:80]}"))
        else:
            print(f"[plan] ✓ Step {i} done: {step_result[:80]}")
            step_results.append((step, step_result[:80]))
            completed += 1

        # Update browser URL context if this was a navigate/search step
        if _BROWSER_AVAILABLE and is_browser_command(step):
            try:
                from browser.dom_reader import get_dom_reader
                reader = get_dom_reader()
                if reader.is_available():
                    tab = reader.get_active_tab()
                    if tab:
                        _session.last_browser_url = tab.get('url', '')
            except Exception:
                pass

    # Build summary
    total = len(plan.steps)
    if completed == total:
        summary = f"All {total} steps completed successfully."
    elif completed == 0:
        summary = f"Task failed — no steps completed out of {total}."
    else:
        summary = (
            f"Completed {completed}/{total} steps. "
            f"Failed: {', '.join(failed_steps[:3])}"
        )

    # Store step results in session for follow-up context
    _session.last_step_results = step_results
    _session.add(goal, summary)

    print(f"\n[plan] {summary}")
    if tts:
        tts.speak(summary)
    _refocus_terminal()
    return summary


def _execute_single_step(step: str, tts=None) -> str:
    """
    Execute one sub-task step through the appropriate handler.
    Returns a result string.
    """
    try:
        # File operation
        if _FILES_AVAILABLE and is_file_command(step):
            return run_file_command(step, tts=tts)

        # Browser operation
        if _BROWSER_AVAILABLE and is_browser_command(step):
            return run_browser_command(step, tts=tts)

        # Screen reader
        if _READER_AVAILABLE and is_screen_read_command(step):
            run_screen_reader(step, tts=tts)
            return "Screen read complete."

        # General UI-TARS loop
        return run_task(step)

    except KeyboardInterrupt:
        request_stop()
        return "FAIL: cancelled by user"


# ── Phase 7+ — LLM-routed task execution ─────────────────────────────────

def run_llm_routed_task(task: str, tts=None) -> str:
    """
    Classify the user's command with Qwen2.5-7B and route to the correct handler.

    Model responsibilities:
        Qwen2.5-7B (text)  → intent classification + text summarization
        Qwen2-VL   (vision) → screen/desktop visual description
        UI-TARS    (vision+action) → GUI automation (clicks, forms, etc.)

    Falls back to regex parsers + UI-TARS if LLM is unavailable.

    Args:
        task: User's natural language command.
        tts:  Optional TTS instance for spoken output.

    Returns:
        Result string describing what was accomplished.
    """
    if not _INTENT_LLM_AVAILABLE:
        return _execute_single_step(task, tts=tts)

    print(f"[intent] Classifying: {task!r}")
    intent = classify_intent(task)

    if intent is None:
        print("[intent] LLM unavailable — falling back to regex routing")
        return _execute_single_step(task, tts=tts)

    intent_type = intent.get("type", "general")
    print(f"[intent] → type={intent_type}, op={intent.get('op','')}")

    return _execute_intent(intent, task, tts=tts)


def _execute_intent(intent: dict, original_task: str, tts=None) -> str:
    """
    Execute a classified intent dict through the appropriate handler.

    Args:
        intent:        Structured intent from classify_intent().
        original_task: Original user text (for fallback + session logging).
        tts:           Optional TTS instance.

    Returns:
        Result string.
    """
    intent_type = intent.get("type", "general")

    try:
        # ── Multi-step: classify and execute each sub-step ────────────────
        if intent_type == "multi_step":
            steps = intent.get("steps", [])
            if not steps:
                return run_task(original_task)

            print(f"[intent] Multi-step plan: {len(steps)} steps")
            for i, step in enumerate(steps, 1):
                print(f"[intent] Step {i}/{len(steps)}: {step!r}")

            # Re-use run_multi_step_task with the pre-decomposed steps
            from dataclasses import dataclass

            class _PrebuiltPlan:
                def __init__(self, original, steps):
                    self.original = original
                    self.steps    = steps
                    self.is_multi = len(steps) > 1

            # Execute steps one by one using LLM intent for each
            results = []
            for i, step in enumerate(steps, 1):
                if STOP_REQUESTED:
                    break
                print(f"\n[intent] ── Step {i}/{len(steps)}: {step!r} ──")
                sub_intent = classify_intent(step) if _INTENT_LLM_AVAILABLE else None
                if sub_intent:
                    res = _execute_intent(sub_intent, step, tts=tts if i == len(steps) else None)
                else:
                    res = _execute_single_step(step, tts=tts if i == len(steps) else None)
                results.append(res)
                print(f"[intent] ✓ Step {i}: {res[:60]}")

            summary = f"Completed {len(results)}/{len(steps)} steps."
            _session.add(original_task, summary)
            if tts:
                tts.speak(summary)
            _refocus_terminal()
            return summary

        # ── Screen read: use Qwen2-VL for desktop, DOM+text for webpages ──
        elif intent_type == "screen_read":
            # First try: browser DOM extraction + text summarization (fastest)
            if _BROWSER_AVAILABLE and _SUMMARIZER_AVAILABLE:
                try:
                    from browser.dom_reader import get_dom_reader
                    reader = get_dom_reader()
                    if reader.is_available():
                        dom_text = reader.get_page_text()
                        if dom_text and len(dom_text.strip()) > 50:
                            # Phase 8: content safety — sanitize webpage text before model
                            safe, warn = check_content_safety(dom_text)
                            if not safe:
                                print(f"[safety] {warn}")
                                dom_text = sanitize_content(dom_text)
                            print("[intent] Using DOM text + Qwen2.5 for webpage summary")
                            summary = summarize_text(dom_text, context=original_task)
                            _session.add(original_task, summary[:200])
                            if tts:
                                tts.speak(summary)
                            return summary
                except Exception:
                    pass

            # Fallback: screenshot → Qwen2-VL
            if _SUMMARIZER_AVAILABLE:
                try:
                    from screen.capture import capture_screen
                    screenshot = capture_screen()
                    print("[intent] Using Qwen2-VL for screen description")
                    summary = summarize_screen(screenshot, context=original_task)
                    # Phase 8: safety check on the AI-generated description text
                    safe, warn = check_content_safety(summary)
                    if not safe:
                        print(f"[safety] Injection detected in screen content: {warn}")
                        summary = sanitize_content(summary)
                    _session.add(original_task, summary[:200])
                    if tts:
                        tts.speak(summary)
                    return summary
                except Exception as e:
                    print(f"[intent] Vision summarizer failed: {e}")

            # Final fallback: legacy OCR reader
            if _READER_AVAILABLE:
                run_screen_reader(original_task, tts=tts)
                return "Screen read complete."
            return "Screen read not available."

        # ── Browser command: route to BrowserHandler ──────────────────────
        elif intent_type == "browser":
            if not _BROWSER_AVAILABLE:
                return "Browser module not available."
            # Convert intent dict to a fake task string that BrowserHandler understands
            # OR call handler methods directly based on op
            op      = intent.get("op", "")
            browser = intent.get("browser", "chrome")
            handler = get_browser_handler()

            if op == "open_browser":
                msg = handler.open_browser(browser=browser)
            elif op == "navigate":
                url = intent.get("url", "")
                msg = handler.navigate(url, browser=browser) if url else \
                      "No URL provided."
            elif op == "search":
                query  = intent.get("query", "")
                engine = intent.get("engine", "google")
                msg = handler.search(query, browser=browser, engine=engine) if query else \
                      "No search query provided."
            elif op == "back":
                msg = handler.go_back(browser=browser)
            elif op == "forward":
                msg = handler.go_forward(browser=browser)
            elif op == "refresh":
                msg = handler.refresh(browser=browser)
            elif op == "new_tab":
                msg = handler.new_tab(browser=browser)
            elif op == "close_tab":
                msg = handler.close_tab(browser=browser)
            elif op == "read_page":
                # Read page → use summarizer if available
                if _SUMMARIZER_AVAILABLE:
                    try:
                        from browser.dom_reader import get_dom_reader
                        reader   = get_dom_reader()
                        dom_text = reader.get_page_text() if reader.is_available() else ""
                        if dom_text and len(dom_text.strip()) > 50:
                            msg = summarize_text(dom_text, context="Read this webpage")
                        else:
                            from screen.capture import capture_screen
                            msg = summarize_screen(capture_screen(), context="webpage")
                    except Exception as e:
                        msg = handler.read_page(query=original_task)
                else:
                    msg = handler.read_page(query=original_task)
            else:
                # Unknown op — fall through to regex browser routing
                return run_browser_command(original_task, tts=tts)

            print(f"\n[browser] {msg[:200]}\n")
            _session.add(original_task, msg[:200])
            if tts:
                spoken = msg if len(msg) <= 300 else msg[:300] + "."
                tts.speak(spoken)
            _refocus_terminal()
            return msg

        # ── File command: route to FileHandler ────────────────────────────
        elif intent_type == "file":
            if not _FILES_AVAILABLE:
                return "File module not available."

            # Phase 8+9: delete/remove always requires explicit confirmation
            file_op = intent.get("op", "")
            if file_op in ("delete", "remove"):
                filename = intent.get("filename", "")
                # Phase 9: resolve vague "this file" reference to session context
                if not filename or filename.lower() in ("this file", "it", "the file", ""):
                    if _session.last_file_path:
                        filename = _session.last_file_path
                        print(f"[intent] Resolved 'this file' → {filename!r} from session")
                    else:
                        return (
                            "I don't know which file to delete. "
                            "Please say 'delete [filename]' with the specific file name."
                        )
                allowed = confirm_sensitive_action(
                    reason=f"This will permanently delete '{filename}'. This cannot be undone.",
                    action_description=f"Delete: {filename}",
                    tts=tts,
                )
                if not allowed:
                    return f"Delete cancelled — '{filename}' was NOT deleted."

            # Reuse the existing FileHandler without duplication
            return run_file_command(original_task, tts=tts)

        # ── App launch: open desktop apps directly via executor ────────────────
        elif intent_type == "app":
            app_name = intent.get("name", "").strip()
            if not app_name:
                print("[intent] App intent missing 'name' — falling to UI-TARS")
                return run_task(original_task)
            try:
                print(f"[intent] Launching app: {app_name!r}")
                result = execute_action({"type": "open_app", "name": app_name})
                msg = f"Opened {app_name}."
                _session.add(original_task, msg)
                if tts:
                    tts.speak(msg)
                _refocus_terminal()
                return msg
            except Exception as e:
                print(f"[intent] open_app failed ({e}) — falling to UI-TARS")
                return run_task(original_task)

        # ── General: visual task → UI-TARS screenshot loop ──────────────────
        else:
            print("[intent] General visual task → UI-TARS")
            return run_task(original_task)

    except KeyboardInterrupt:
        request_stop()
        return "FAIL: cancelled by user"
    except Exception as e:
        print(f"[intent] Execution error: {e} — falling back to UI-TARS")
        return run_task(original_task)


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

            command = command.strip()
            print(f"[Voice] Command: {command!r}")

            # Noise filter: only reject TRUE audio noise, not natural language.
            # The LLM handles ALL natural language — don't second-guess it here.
            # Only reject:
            #   - Very short output (≤2 chars) — microphone blip
            #   - >50% non-ASCII — definitely not English speech (hardware noise)
            def _is_audio_noise(text: str) -> bool:
                if len(text.strip()) <= 2:
                    return True
                non_ascii = sum(1 for c in text if ord(c) > 127)
                if non_ascii / max(len(text), 1) > 0.50:
                    return True
                return False

            if _is_audio_noise(command):
                print(f"[Voice] Audio noise detected: {command!r} — ignoring")
                tts.speak("I didn't catch that. Please say Hey Access and try again.")
                continue


            if any(w in command.lower() for w in ("stop", "quit", "exit", "goodbye")):
                tts.speak("Goodbye. Stopping voice mode.")
                break

            # Bug fix #4: use speak() synchronously so TTS finishes before agent starts
            # (prevents own voice triggering the next wake-word detection)
            tts.speak(f"Running: {command}")

            try:
                # Phase 9: voice routes through the same LLM intent pipeline as text
                if _INTENT_LLM_AVAILABLE:
                    result = run_llm_routed_task(command, tts=tts)
                    print(f"[Voice] Result: {result[:80]}")
                else:
                    # Legacy regex fallback when LLM unavailable
                    if _READER_AVAILABLE and is_screen_read_command(command):
                        run_screen_reader(command, tts=tts)
                    elif _PLANNER_AVAILABLE and is_multi_step(command):
                        run_multi_step_task(command, tts=tts)
                    elif _FILES_AVAILABLE and is_file_command(command):
                        run_file_command(command, tts=tts)
                    elif _BROWSER_AVAILABLE and is_browser_command(command):
                        run_browser_command(command, tts=tts)
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
    print("  AccessOS -- AI Computer-Use Agent (Phase 9)")
    print("  Intent:      Qwen2.5-7B-Instruct (fast text)")
    print("  Summarizer:  Qwen2-VL-7B-Instruct (vision)")
    print("  GUI Actions: UI-TARS-1.5-7B (visual automation)")
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
    if _INTENT_LLM_AVAILABLE:
        modules.append("LLM Intent (Qwen2.5-7B)")
    if _SUMMARIZER_AVAILABLE:
        modules.append("Vision Summarizer (Qwen2-VL)")
    if _BROWSER_AVAILABLE:
        modules.append("Browser Automation")
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

        # ── Routing (Phase 7+) ────────────────────────────────────────────
        # PRIMARY: LLM intent classifier (Qwen2.5-7B) — understands natural language
        # FALLBACK: regex parsers → UI-TARS (if LLM unavailable/timeout)
        tts_instance = _get_tts()
        try:
            if _INTENT_LLM_AVAILABLE:
                result = run_llm_routed_task(task, tts=tts_instance)
            else:
                # Legacy regex routing fallback
                if _PLANNER_AVAILABLE and is_multi_step(task):
                    result = run_multi_step_task(task, tts=tts_instance) or ""
                elif _READER_AVAILABLE and is_screen_read_command(task):
                    run_screen_reader(task, tts=tts_instance)
                    result = "Screen read complete."
                elif _FILES_AVAILABLE and is_file_command(task):
                    result = run_file_command(task, tts=tts_instance) or ""
                elif _BROWSER_AVAILABLE and is_browser_command(task):
                    result = run_browser_command(task, tts=tts_instance) or ""
                else:
                    result = run_task(task)
        except KeyboardInterrupt:
            request_stop()
            print("\n[AccessOS] Task cancelled by user (Ctrl+C).\n")
            continue

        print("\n[AccessOS] Done: {}\n".format(result[:120]))


if __name__ == "__main__":
    main()
