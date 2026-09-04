# AccessOS — Speech-to-Text (STT)
# Uses the SpeechRecognition library with Google Web Speech API.
# Modular: swap _transcribe() to use Whisper, Azure, or another provider.

import time
from typing import Optional

try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False

try:
    import pyaudio  # noqa: F401 — imported only to check availability
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False


class STT:
    """
    Speech-to-Text using the SpeechRecognition library.

    Primary backend: Google Web Speech API (requires internet).
    Fallback:        CMU Sphinx (offline, lower accuracy) if installed.

    Usage:
        stt = STT()
        stt.calibrate()           # one-time ambient-noise calibration
        text = stt.listen_once()  # blocks until speech or timeout
    """

    def __init__(
        self,
        listen_timeout: float = 8.0,
        phrase_time_limit: float = 12.0,
        energy_threshold: int = 300,
    ):
        self._timeout = listen_timeout
        self._phrase_limit = phrase_time_limit
        self._calibrated = False

        if not _SR_AVAILABLE:
            print("[STT] SpeechRecognition not installed. Voice input disabled.")
            self.recognizer = None
            return

        if not _PYAUDIO_AVAILABLE:
            print("[STT] PyAudio not installed. Voice input disabled.")
            print("[STT]   Fix: python -m pip install pyaudio")
            self.recognizer = None
            return

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = energy_threshold
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8   # seconds of silence = end of phrase

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self.recognizer is not None

    def calibrate(self, duration: float = 1.5) -> None:
        """Calibrate energy threshold to ambient noise level."""
        if not self.available:
            return
        try:
            with sr.Microphone() as source:
                print("[STT] Calibrating microphone for ambient noise...")
                self.recognizer.adjust_for_ambient_noise(source, duration=duration)
                self._calibrated = True
                print(f"[STT] Calibration done. Energy threshold: "
                      f"{self.recognizer.energy_threshold:.0f}")
        except Exception as e:
            print(f"[STT] Calibration failed: {e}")

    def listen_once(self, prompt: Optional[str] = None) -> Optional[str]:
        """
        Listen for one utterance and return the transcribed string.

        Args:
            prompt: Optional message to print before listening.

        Returns:
            Transcribed text, or None on silence / error.
        """
        if not self.available:
            return None

        if prompt:
            print(f"[STT] {prompt}")

        try:
            with sr.Microphone() as source:
                if not self._calibrated:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.listen(
                    source,
                    timeout=self._timeout,
                    phrase_time_limit=self._phrase_limit,
                )
            return self._transcribe(audio)

        except sr.WaitTimeoutError:
            print("[STT] No speech detected (timeout).")
            return None
        except Exception as e:
            print(f"[STT] Microphone error: {e}")
            return None

    # ── Backend ───────────────────────────────────────────────────────────

    def _transcribe(self, audio) -> Optional[str]:
        """Transcribe audio. Tries Google first, then Sphinx offline."""
        # Primary: Google Web Speech API
        try:
            text = self.recognizer.recognize_google(audio)
            print(f"[STT] Heard: {text!r}")
            return text.strip()
        except sr.UnknownValueError:
            print("[STT] Could not understand audio.")
            return None
        except sr.RequestError as e:
            print(f"[STT] Google STT unavailable ({e}). Trying offline...")

        # Fallback: CMU Sphinx (offline) — only if installed
        try:
            text = self.recognizer.recognize_sphinx(audio)
            print(f"[STT] (Sphinx) Heard: {text!r}")
            return text.strip()
        except Exception:
            print("[STT] All transcription methods failed.")
            return None


# ── Module-level singleton ─────────────────────────────────────────────────

_default_stt: Optional[STT] = None


def get_stt() -> STT:
    global _default_stt
    if _default_stt is None:
        _default_stt = STT()
    return _default_stt
