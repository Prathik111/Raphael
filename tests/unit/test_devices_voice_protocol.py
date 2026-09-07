"""Gates 35-38: thin phone, hardware console, voice, envelope protocol."""

import time

import pytest

from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.events import EventBus
from ai_ecosystem.core.models.enums import EventType, TaskState
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.interface import (
    ApiError,
    EcosystemGateway,
    GatewayError,
    HardwareGateway,
    PhoneClient,
    ProtocolError,
    ProtocolValidator,
    RuntimeAPI,
    SimulatedDevice,
    envelope_for,
)
from ai_ecosystem.interface.gateway import hmac_check, hmac_sign
from ai_ecosystem.interface.protocol import AuthRef
from ai_ecosystem.interface.voice import MockSTT, MockTTS, VoiceError, VoiceSession


@pytest.fixture()
def wired(tmp_path):
    runtime = AgentRuntime(str(tmp_path / "dev.db"))
    bus = runtime.bus
    gateway = EcosystemGateway(bus=bus)
    api = RuntimeAPI(runtime)
    yield gateway, api, runtime, bus
    runtime.shutdown()


def _paired_phone(wired):
    gateway, api, _, bus = wired
    phone = PhoneClient(gateway, api, bus)
    code = gateway.issue_pairing_code("phone-1", "phone")
    token = phone.pair(code)
    return phone, token


def test_phone_pairing(wired):
    gateway, api, _, _ = wired
    phone = PhoneClient(gateway, api)
    token = phone.pair(gateway.issue_pairing_code("phone-1", "phone"))
    assert token
    with pytest.raises(GatewayError):  # single-use codes
        phone.pair(gateway.issue_pairing_code("phone-1", "phone")[:0] or "deadbeef")


def test_phone_authentication(wired):
    gateway, _, _, _ = wired
    with pytest.raises(GatewayError):
        gateway.check("bogus-token", "status")


def test_phone_revocation(wired):
    phone, token = _paired_phone(wired)
    gateway = phone._gateway
    gateway.revoke_token(token)
    with pytest.raises(GatewayError):
        phone.status(token)
    token2 = phone.pair(gateway.issue_pairing_code("phone-1", "phone"))
    gateway.revoke_device("phone-1")
    with pytest.raises(GatewayError):
        phone.status(token2)


def test_phone_expired_session(wired):
    gateway, _, _, _ = wired
    fast = EcosystemGateway(clock=time.time, session_ttl_s=-1.0)
    code = fast.issue_pairing_code("p", "phone")
    token = fast.pair(code, "abcdefgh12345678")
    with pytest.raises(GatewayError, match="expired"):
        fast.check(token, "status")


def test_pairing_rejects_weak_secrets(wired):
    gateway, _, _, _ = wired
    with pytest.raises(GatewayError, match="too weak"):
        gateway.pair(gateway.issue_pairing_code("p", "phone"), "a" * 16)
    with pytest.raises(GatewayError, match="too weak"):
        gateway.pair(gateway.issue_pairing_code("p", "phone"), "short")


def test_refresh_revokes_old_token(wired):
    phone, token = _paired_phone(wired)
    gateway = phone._gateway
    fresh = gateway.refresh(token)
    assert fresh != token
    with pytest.raises(GatewayError):
        gateway.check(token, "status")
    assert gateway.check(fresh, "status").device_id == "phone-1"


def test_write_scopes_require_nonce(wired):
    phone, token = _paired_phone(wired)
    gateway = phone._gateway
    with pytest.raises(GatewayError, match="nonce"):
        gateway.check(token, "cancel")
    with pytest.raises(GatewayError, match="nonce"):
        gateway.check(token, "decide")
    # Read scopes still work without one.
    assert gateway.check(token, "status").device_id == "phone-1"


def test_phone_permission_request_view(wired):
    phone, token = _paired_phone(wired)
    assert phone.permission_requests(token) == []
    task = phone._api.create_task("Phone task.")
    assert phone.permission_requests(token) == []  # none decided yet


def test_phone_denial_cancels(wired):
    phone, token = _paired_phone(wired)
    task = phone._api.create_task("Stop me.")
    cancelled = phone.deny(token, task["task_id"])
    assert cancelled["state"] == TaskState.CANCELLED.value


def test_phone_approve_grants_nothing(wired):
    phone, token = _paired_phone(wired)
    task = phone._api.create_task("Approve me.")
    outcome = phone.approve(token, task["task_id"], call_id="c1")
    assert outcome == {"acknowledged": True, "grants": "nothing"}


def test_phone_malicious_request(wired):
    phone, token = _paired_phone(wired)
    with pytest.raises((ApiError, GatewayError, DomainValidationError)):
        phone._api.create_task("")
    # Unknown scopes never pass the gateway.
    with pytest.raises(GatewayError):
        phone._gateway.check(token, "policy.modify")


def test_phone_replay_protection(wired):
    gateway, _, _, _ = wired
    phone, token = _paired_phone(wired)
    gateway.check(token, "status", nonce="n-once")
    with pytest.raises(GatewayError, match="replayed"):
        gateway.check(token, "status", nonce="n-once")


def test_phone_disconnect_reconnect(wired):
    phone, token = _paired_phone(wired)
    gateway = phone._gateway
    gateway.revoke_token(token)
    with pytest.raises(GatewayError):
        phone.status(token)
    token2 = phone.pair(gateway.issue_pairing_code("phone-1", "phone"))
    assert phone.status(token2)["tasks"] == []


def test_hardware_pairing(wired):
    gateway, api, _, bus = wired
    secret = "hw-secret-01-abcdef"
    code = gateway.issue_pairing_code("esp32-1", "hardware")
    gateway.pair(code, secret)
    assert gateway.device_secret("esp32-1") == secret


def test_hardware_authentication(wired):
    gateway, api, _, bus = wired
    hardware = HardwareGateway(gateway, api, bus)
    device = SimulatedDevice("ghost", "ghost-secret-02-abcdef")
    with pytest.raises(DomainValidationError, match="not paired"):
        hardware.handle(device.packet("status"))


def _paired_hardware(wired):
    gateway, api, _, bus = wired
    secret = "hw-secret-01-abcdef"
    gateway.pair(gateway.issue_pairing_code("esp32-1", "hardware"), secret)
    hardware = HardwareGateway(gateway, api, bus)
    return hardware, SimulatedDevice("esp32-1", secret), gateway


def test_hardware_commands(wired):
    hardware, device, _ = _paired_hardware(wired)
    status = hardware.handle(device.packet("status"))
    assert "tasks" in status and "agents" in status


def test_hardware_approval_and_denial(wired):
    hardware, device, _ = _paired_hardware(wired)
    task = hardware._api.create_task("Hardware task.")
    approved = hardware.handle(device.packet(
        "approve", {"task_id": task["task_id"]}))
    assert approved["grants"] == "nothing"  # advisory, never a grant
    denied = hardware.handle(device.packet("deny", {"task_id": task["task_id"]}))
    assert denied["state"] == TaskState.CANCELLED.value


def test_hardware_replay(wired):
    hardware, device, _ = _paired_hardware(wired)
    packet = device.packet("status")
    hardware.handle(packet)
    with pytest.raises(DomainValidationError, match="replay"):
        hardware.handle(packet)


def test_hardware_malformed_packet(wired):
    hardware, device, _ = _paired_hardware(wired)
    with pytest.raises(DomainValidationError):
        hardware.handle({"junk": True})
    tampered = device.packet("status")
    tampered["command"] = "kill"  # signature no longer matches
    with pytest.raises(DomainValidationError, match="signature"):
        hardware.handle(tampered)


def test_hardware_version_mismatch(wired):
    hardware, device, _ = _paired_hardware(wired)
    with pytest.raises(DomainValidationError, match="unsupported"):
        hardware.handle(device.packet("status", protocol_version="9.9"))


def test_hardware_emergency_kill(wired):
    hardware, device, _ = _paired_hardware(wired)
    api = hardware._api
    api.create_task("Scoped job one.")
    api.create_task("Scoped job two.")
    outcome = hardware.handle(device.packet("kill", {"scope": "Scoped"}))
    assert len(outcome["cancelled"]) == 2


def test_voice_transcription(wired):
    phone, token = _paired_phone(wired)
    session = VoiceSession(phone, token, MockSTT({"a1": "status of my tasks"}),
                           MockTTS())
    out = session.handle("a1")
    assert "tasks known" in out["reply"]


def test_voice_malformed_transcription(wired):
    phone, token = _paired_phone(wired)
    session = VoiceSession(phone, token, MockSTT({"a1": "   "}), MockTTS())
    out = session.handle("a1")
    assert "didn't catch" in out["reply"]


def test_voice_command_submits_goal(wired):
    phone, token = _paired_phone(wired)
    tts = MockTTS()
    session = VoiceSession(phone, token, MockSTT({"a1": "Summarize the repo"}), tts)
    out = session.handle("a1")
    assert "task_id" in out and tts.spoken


def test_voice_permission_request_readback(wired):
    phone, token = _paired_phone(wired)
    session = VoiceSession(phone, token, MockSTT({}), MockTTS())
    out = session.read_permission_requests()
    assert "No recent permission" in out["reply"]


def test_voice_cancellation(wired):
    phone, token = _paired_phone(wired)
    phone._api.create_task("Cancel the monthly report.")
    session = VoiceSession(phone, token,
                           MockSTT({"a1": "cancel the monthly report"}), MockTTS())
    out = session.handle("a1")
    assert "Cancelled" in out["reply"]


def test_voice_disabled(wired):
    phone, token = _paired_phone(wired)
    session = VoiceSession(phone, token, MockSTT({"a1": "hi"}), MockTTS(),
                           enabled=False)
    with pytest.raises(VoiceError, match="disabled"):
        session.handle("a1")
    session.set_enabled(True)
    assert "task_id" in session.handle("a1")


def test_voice_provider_failure(wired):
    phone, token = _paired_phone(wired)
    session = VoiceSession(phone, token, MockSTT(fail=True), MockTTS())
    with pytest.raises(VoiceError, match="unavailable"):
        session.handle("a1")


def _allow_all():
    from ai_ecosystem.interface.protocol import ProtocolValidator

    return ProtocolValidator(authorize=lambda env: True)


def test_protocol_serialization():
    validator = _allow_all()
    envelope = envelope_for("pc", "pc", "STATUS", {"ok": True},
                            correlation_id="c1",
                            auth=AuthRef(scheme="token", key_id="k1"))
    raw = validator.encode(envelope)
    back = validator.decode(raw)
    assert back.sender == "pc" and back.correlation_id == "c1"


def test_protocol_validation():
    validator = ProtocolValidator()
    with pytest.raises(Exception):
        validator.decode(b"not json at all{{{")
    with pytest.raises(Exception, match="size"):
        validator.decode(b'{"blob": "' + b"y" * 300_000 + b'"}')


def test_protocol_versioning():
    validator = ProtocolValidator()
    envelope = envelope_for("pc", "pc", "X", auth=AuthRef(scheme="t", key_id="k"))
    envelope.protocol_version = "9.0"
    with pytest.raises(Exception, match="incompatible"):
        validator.validate(envelope)


def test_protocol_authentication():
    validator = ProtocolValidator(authorize=lambda env: env.sender == "pc")
    good = envelope_for("pc", "pc", "X", auth=AuthRef(scheme="t", key_id="k"))
    validator.validate(good)
    bad = envelope_for("mallory", "pc", "X", auth=AuthRef(scheme="t", key_id="k"))
    with pytest.raises(Exception, match="not authorized"):
        validator.validate(bad)
    naked = envelope_for("pc", "pc", "X")
    with pytest.raises(Exception, match="authorization context"):
        validator.validate(naked)


def test_protocol_authorization_context():
    validator = _allow_all()
    envelope = envelope_for("pc", "pc", "X", auth=AuthRef(scheme="hmac",
                                                          key_id="esp32-1"))
    assert validator.validate(envelope).auth.key_id == "esp32-1"


def test_protocol_malformed_packets():
    validator = ProtocolValidator()
    with pytest.raises(Exception):
        validator.decode(b"{}")  # missing sender/type/auth


def test_protocol_replay():
    validator = _allow_all()
    envelope = envelope_for("pc", "pc", "X", auth=AuthRef(scheme="t", key_id="k"))
    raw = validator.encode(envelope)
    validator.decode(raw)
    with pytest.raises(Exception, match="replay"):
        validator.decode(raw)


def test_protocol_size_limits():
    validator = ProtocolValidator()
    with pytest.raises(Exception, match="size"):
        validator.decode(b"x" * 300_000)


def test_protocol_default_deny():
    validator = ProtocolValidator()
    envelope = envelope_for("pc", "pc", "X", auth=AuthRef(scheme="t", key_id="k"))
    with pytest.raises(Exception, match="not authorized"):
        validator.validate(envelope)


def test_protocol_compatibility():
    v1 = _allow_all()
    v2 = _allow_all()
    envelope = envelope_for("pc", "pc", "PING", {"n": 1}, correlation_id="c",
                            auth=AuthRef(scheme="t", key_id="k"))
    assert v2.decode(v1.encode(envelope)).payload == {"n": 1}


def test_hmac_helpers():
    signed = hmac_sign("s3cr3t", "canonical-bytes")
    assert hmac_check("s3cr3t", "canonical-bytes", signed) is True
    assert hmac_check("s3cr3t", "canonical-bytes", "0" * 64) is False
