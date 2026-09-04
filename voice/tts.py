# AccessOS — Text-to-Speech (TTS)
# Uses pyttsx3 for fully offline Windows SAPI voices.
# This module is designed to be swapped out for another TTS provider easily.

import threading
from typing import Optional

try:
    import pyttsx3
    _PYTTSX3_AVAILABLE = True
except ImportError:
    _PYTTSX3_AVAILABLE = False


class TTS:
    """
    Text-to-Speech engine wrapping pyttsx3 (Windows SAPI, fully offline).

    Usage:
        tts = TTS()
        tts.speak("Calculator is now open.")
    """

    def __init__(self, rate: int = 170, volume: float = 0.95):
        self._engine = None
        self._rate = rate
        self._volume = volume
        self._lock = threading.Lock()
        self._init_engine()

    def _init_engine(self):
        if not _PYTTSX3_AVAILABLE:
            print("[TTS] pyttsx3 not installed. Voice output disabled.")
            return
        try:
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self._rate)
            self._engine.setProperty("volume", self._volume)
            # Prefer a female voice if available (purely aesthetic)
            voices = self._engine.getProperty("voices")
            for v in voices:
                if "zira" in v.name.lower() or "female" in v.name.lower():
                    self._engine.setProperty("voice", v.id)
                    break
        except Exception as e:
            print(f"[TTS] Engine init failed: {e}")
            self._engine = None

    def speak(self, text: str) -> None:
        """
        Speak *text* synchronously (blocks until audio finishes).
        Prints the text regardless so text-mode users see the response.
        """
        print(f"[TTS] {text}")
        if not self._engine:
            return
        try:
            with self._lock:
                self._engine.say(text)
                self._engine.runAndWait()
        except Exception as e:
            print(f"[TTS] Speak error: {e}")

    def speak_async(self, text: str) -> None:
        """Speak *text* in a daemon thread so the main loop is not blocked."""
        t = threading.Thread(target=self.speak, args=(text,), daemon=True)
        t.start()

    @property
    def available(self) -> bool:
        return self._engine is not None


# Module-level singleton — import and use directly if preferred.
_default: Optional[TTS] = None


def get_tts() -> TTS:
    global _default
    if _default is None:
        _default = TTS()
    return _default


def speak(text: str) -> None:
    """Convenience function: speak using the module-level TTS singleton."""
    get_tts().speak(text)
