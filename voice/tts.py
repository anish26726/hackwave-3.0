# AccessOS — Text-to-Speech (TTS)
# Uses Windows SAPI directly via comtypes, bypassing pyttsx3 entirely.
# This completely eliminates threading and event loop crashes.

import threading
from typing import Optional

try:
    import comtypes.client
    _SAPI_AVAILABLE = True
except ImportError:
    _SAPI_AVAILABLE = False


class TTS:
    """
    Text-to-Speech engine wrapping Windows SAPI.
    """

    def __init__(self, rate: int = 2, volume: int = 95):
        self._rate = rate
        self._volume = volume
        self._available = _SAPI_AVAILABLE

        if not self._available:
            print("[TTS] comtypes not installed. Voice output disabled.")
            return

        # We initialize COM for the main thread just in case.
        # But we create the speaker object per-thread when speak is called.
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

    def _get_speaker(self):
        """Creates a fresh SpVoice object for the current thread."""
        try:
            import comtypes
            comtypes.CoInitialize()
            speaker = comtypes.client.CreateObject("SAPI.SpVoice")
            speaker.Rate = self._rate
            speaker.Volume = self._volume
            
            # Prefer Zira (female) if available
            voices = speaker.GetVoices()
            for i in range(voices.Count):
                name = voices.Item(i).GetDescription()
                if "Zira" in name or "female" in name.lower():
                    speaker.Voice = voices.Item(i)
                    break
            return speaker
        except Exception as e:
            print(f"[TTS] SAPI initialization failed: {e}")
            return None

    def speak(self, text: str) -> None:
        """Speak synchronously."""
        print(f"[TTS] {text}")
        if not self._available:
            return
            
        speaker = self._get_speaker()
        if speaker:
            try:
                # Speak synchronously
                speaker.Speak(text)
            except Exception as e:
                print(f"[TTS] Speak error: {e}")

    def speak_async(self, text: str) -> None:
        """Speak asynchronously in a background thread (non-blocking)."""
        # Don't print here — caller already logs the message.
        # Printing from a background thread causes garbled stdout.
        if not self._available:
            return

        t = threading.Thread(target=self._speak_thread, args=(text,), daemon=True)
        t.start()

    def _speak_thread(self, text: str) -> None:
        speaker = self._get_speaker()
        if speaker:
            try:
                speaker.Speak(text)
            except Exception as e:
                print(f"[TTS] Async speak error: {e}")

    @property
    def available(self) -> bool:
        return self._available


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
