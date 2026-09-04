# AccessOS — Wake Word Detector
# Continuously monitors the microphone and fires a callback when the
# wake phrase "Hey Access" (or variants) is detected.
# Modular: replace _contains_wake_word() or the backend for a different provider.

import time
from typing import Callable, Optional

try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False

try:
    import pyaudio  # noqa: F401
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False


# All phrases that count as the wake word (handles STT mishearings)
DEFAULT_WAKE_WORDS = {
    "hey access",
    "hey axis",
    "hey axes",
    "hi access",
    "okay access",
    "ok access",
    "access",       # short-form trigger also accepted
}


class WakeWordDetector:
    """
    Lightweight wake-word detector using continuous mic listening + keyword match.

    Usage:
        detector = WakeWordDetector()
        detector.listen_loop(on_wake=my_callback)   # blocks forever
    """

    def __init__(
        self,
        wake_words: Optional[set] = None,
        chunk_duration: float = 2.0,   # seconds to listen per chunk (was 3.0)
    ):
        self.wake_words = wake_words or DEFAULT_WAKE_WORDS
        self.chunk_duration = chunk_duration
        self._running = False

        if not _SR_AVAILABLE or not _PYAUDIO_AVAILABLE:
            print("[WakeWord] SpeechRecognition/PyAudio not available. "
                  "Wake-word detection disabled.")
            self.recognizer = None
            return

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.6

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self.recognizer is not None

    def listen_loop(self, on_wake: Callable[[], None]) -> None:
        """
        Block indefinitely, calling *on_wake* each time the wake word is heard.
        Runs until stop() is called.

        Args:
            on_wake: Zero-argument callable invoked on each wake-word detection.
        """
        if not self.available:
            print("[WakeWord] Not available — cannot start listen loop.")
            return

        self._running = True
        print(f"[WakeWord] Listening for wake word "
              f"({', '.join(sorted(self.wake_words))!r})...")

        while self._running:
            text = self._listen_chunk()
            if text and self._contains_wake_word(text):
                print(f"[WakeWord] Wake word detected in: {text!r}")
                try:
                    on_wake()
                except Exception as e:
                    print(f"[WakeWord] on_wake callback error: {e}")
                # Brief pause after callback before resuming listening
                time.sleep(0.2)

    def stop(self) -> None:
        """Signal the listen loop to stop after the current chunk."""
        self._running = False

    def wait_for_wake_word(self) -> bool:
        """
        Block until the wake word is detected once.
        Returns True when detected, False if stopped.
        """
        if not self.available:
            return False

        print("[WakeWord] Waiting for 'Hey Access'...")
        self._running = True
        while self._running:
            text = self._listen_chunk()
            if text and self._contains_wake_word(text):
                print(f"[WakeWord] Detected: {text!r}")
                return True
        return False

    # ── Internals ─────────────────────────────────────────────────────────

    def _listen_chunk(self) -> Optional[str]:
        """Listen for one short audio chunk and return transcription or None."""
        try:
            with sr.Microphone() as source:
                # B5 fix: removed per-chunk adjust_for_ambient_noise()
                # It added a 200 ms dead-zone before every chunk and caused
                # the detector to miss fast wake words.
                # dynamic_energy_threshold=True handles noise adaptation instead.
                audio = self.recognizer.listen(
                    source,
                    timeout=None,                       # wait as long as needed
                    phrase_time_limit=self.chunk_duration,
                )
            return self._transcribe_quick(audio)
        except Exception as e:
            print(f"[WakeWord] Listen error: {e}")
            time.sleep(0.5)
            return None

    def _transcribe_quick(self, audio) -> Optional[str]:
        """Fast transcription — uses Google STT, ignores errors silently."""
        try:
            return self.recognizer.recognize_google(audio).lower()
        except sr.UnknownValueError:
            return None
        except sr.RequestError:
            return None
        except Exception:
            return None

    def _contains_wake_word(self, text: str) -> bool:
        """Return True if any wake word phrase appears in *text*."""
        t = text.lower().strip()
        return any(ww in t for ww in self.wake_words)
