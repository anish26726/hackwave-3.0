# AccessOS — Speech-to-Text (STT)
# Modular STT with three backends in priority order:
#   1. Google Web Speech API (online, high accuracy)
#   2. Local Whisper model (offline, good accuracy — requires: pip install openai-whisper)
#   3. CMU Sphinx (offline, basic accuracy — requires: pip install pocketsphinx)
#
# L1 fix: Whisper backend added so voice mode works fully offline.

import os
import time
import tempfile
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

# Optional: Whisper offline backend (L1 fix)
try:
    import whisper as _whisper_lib
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False

# Lazy-loaded Whisper model singleton (avoids 1–2 GB load on every call)
_whisper_model = None


def _get_whisper_model():
    """Load and cache the Whisper 'base' model (≈140 MB, good accuracy/speed balance)."""
    global _whisper_model
    if _whisper_model is None:
        model_name = os.environ.get('WHISPER_MODEL', 'base')
        print(f"[STT] Loading Whisper '{model_name}' model (first run may take a moment)…")
        _whisper_model = _whisper_lib.load_model(model_name)
        print("[STT] Whisper model ready.")
    return _whisper_model


class STT:
    """
    Speech-to-Text using the SpeechRecognition library.

    Backend priority:
        1. Google Web Speech API (online, ~high accuracy)
        2. Local Whisper model   (offline, ~good accuracy — pip install openai-whisper)
        3. CMU Sphinx            (offline, ~basic accuracy)

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

        # Report available backends at startup
        backends = ["Google Web Speech (online)"]
        if _WHISPER_AVAILABLE:
            backends.append("Whisper (offline)")
        backends.append("Sphinx (offline, if installed)")
        print(f"[STT] Available backends: {', '.join(backends)}")

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
        """
        Transcribe audio using available backends in priority order:
            1. Google Web Speech (online)
            2. Whisper (offline, if openai-whisper installed)
            3. Sphinx  (offline, if pocketsphinx installed)
        """
        # ── Backend 1: Google Web Speech API ──────────────────────────────
        try:
            text = self.recognizer.recognize_google(audio)
            print(f"[STT] (Google) Heard: {text!r}")
            return text.strip()
        except sr.UnknownValueError:
            print("[STT] Could not understand audio.")
            return None
        except sr.RequestError as e:
            print(f"[STT] Google STT unavailable ({e}). Trying offline backend…")

        # ── Backend 2: Whisper (offline) — L1 fix ─────────────────────────
        if _WHISPER_AVAILABLE:
            result = self._transcribe_whisper(audio)
            if result:
                return result
            print("[STT] Whisper failed. Trying Sphinx…")

        # ── Backend 3: CMU Sphinx (offline fallback) ─────────────────────
        try:
            text = self.recognizer.recognize_sphinx(audio)
            print(f"[STT] (Sphinx) Heard: {text!r}")
            return text.strip()
        except Exception:
            pass

        print("[STT] All transcription backends failed.")
        return None

    def _transcribe_whisper(self, audio) -> Optional[str]:
        """
        Transcribe using the local Whisper model (offline, no internet required).
        Saves audio to a temporary WAV file, then runs Whisper on it.
        """
        tmp_path = None
        try:
            model = _get_whisper_model()
            # Write audio to a temporary WAV file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                f.write(audio.get_wav_data())
                tmp_path = f.name
            result = model.transcribe(tmp_path, language='en', fp16=False)
            text = result.get('text', '').strip()
            if text:
                print(f"[STT] (Whisper) Heard: {text!r}")
                return text
            return None
        except Exception as e:
            print(f"[STT] Whisper error: {e}")
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


# ── Module-level singleton ─────────────────────────────────────────────────

_default_stt: Optional[STT] = None


def get_stt() -> STT:
    global _default_stt
    if _default_stt is None:
        _default_stt = STT()
    return _default_stt
