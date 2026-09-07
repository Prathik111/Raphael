"""Voice as another thin interface to the same runtime (Gate 37).

Speech providers are injected (mocks in tests); transcripts become
ordinary goals through the phone client, so planning, risk, policy,
and execution apply unchanged. Capture is explicit per call -- there
is no background recording, no audio storage, and a master switch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ai_ecosystem.core.errors.exceptions import AiEcosystemError, DomainValidationError
from ai_ecosystem.interface.phone import PhoneClient


class VoiceError(AiEcosystemError):
    """Voice failure (disabled session, bad transcript, provider outage)."""


class STTProvider(ABC):
    """Speech-to-text backend (audio in, text out)."""

    @abstractmethod
    def transcribe(self, audio_ref: str) -> str:
        """Return the transcript for an audio reference."""
        raise NotImplementedError


class TTSProvider(ABC):
    """Text-to-speech backend (text in, speech out)."""

    @abstractmethod
    def speak(self, text: str) -> str:
        """Vocalize text; returns a synthesis reference."""
        raise NotImplementedError


class MockSTT(STTProvider):
    """Scripted transcripts for tests (optionally failing)."""

    def __init__(self, transcripts: Optional[dict[str, str]] = None,
                 fail: bool = False) -> None:
        self._transcripts = dict(transcripts or {})
        self._fail = fail
        self.calls: list[str] = []

    def transcribe(self, audio_ref: str) -> str:
        """Look up the canned transcript (or fail loudly)."""
        self.calls.append(audio_ref)
        if self._fail:
            raise VoiceError("speech provider unavailable")
        return self._transcripts.get(audio_ref, "")


class MockTTS(TTSProvider):
    """Records everything 'spoken' for assertions."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> str:
        """Record the utterance; return a fake reference."""
        self.spoken.append(text)
        return f"tts:{len(self.spoken)}"


class VoiceSession:
    """One explicit voice interaction session (opt-in per session)."""

    def __init__(
        self,
        phone: PhoneClient,
        token: str,
        stt: STTProvider,
        tts: TTSProvider,
        enabled: bool = True,
    ) -> None:
        self._phone = phone
        self._token = token
        self._stt = stt
        self._tts = tts
        self._enabled = enabled

    def set_enabled(self, enabled: bool) -> None:
        """Operator master switch for voice capture."""
        self._enabled = enabled

    def handle(self, audio_ref: str) -> dict[str, Any]:
        """Transcribe once, route the intent, speak the outcome."""
        if not self._enabled:
            raise VoiceError("voice session is disabled")
        try:
            text = self._stt.transcribe(audio_ref)
        except VoiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VoiceError(f"transcription failed: {exc}") from exc
        if not isinstance(text, str) or not text.strip():
            reply = "I didn't catch that. Please try again."
            return {"reply": reply, "audio": self._tts.speak(reply)}
        lowered = text.strip().lower()
        if lowered.startswith("status"):
            status = self._phone.status(self._token)
            reply = f"{len(status['tasks'])} tasks known."
            return {"reply": reply, "audio": self._tts.speak(reply)}
        if lowered.startswith("cancel "):
            title = lowered[len("cancel "):].strip()
            tasks = [t for t in self._phone.status(self._token)["tasks"]
                     if title in t["title"].lower() and not t["completed"]]
            if not tasks:
                reply = "No matching open task."
                return {"reply": reply, "audio": self._tts.speak(reply)}
            self._phone.deny(self._token, tasks[0]["task_id"])
            reply = f"Cancelled {tasks[0]['title']}."
            return {"reply": reply, "audio": self._tts.speak(reply)}
        created = self._phone.submit_voice_goal(self._token, text.strip())
        reply = f"Task {created['task_id']} submitted."
        return {"reply": reply, "audio": self._tts.speak(reply),
                "task_id": created["task_id"]}

    def read_permission_requests(self, limit: int = 5) -> dict[str, Any]:
        """Speak back recent permission decisions (read-only)."""
        if not self._enabled:
            raise VoiceError("voice session is disabled")
        decisions = self._phone.permission_requests(self._token, limit)
        if not decisions:
            reply = "No recent permission decisions."
        else:
            reply = "; ".join(
                f"{d['payload'].get('tool', 'a tool')} was "
                f"{'allowed' if d['type'] == 'PermissionGranted' else 'denied'}"
                for d in decisions)
        return {"reply": reply, "audio": self._tts.speak(reply)}
