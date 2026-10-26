"""Unit tests for the in-GCP API-key rotation and check jobs (ADR-0007).

No GCP: ``SecretStore`` is faked. The contracts pinned here are the ones that
matter — the key is generated (never supplied), the public placeholder is
refused, a store failure writes nothing else, and no job ever prints a value.
"""

from __future__ import annotations

import base64
import json

import pytest
from agentic_kg import check_api_key as check_mod
from agentic_kg import rotate_api_key_gcp as rotate_mod


class _Store:
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []

    def add_version(self, secret_id: str, value: str) -> str:
        self.added.append((secret_id, value))
        return f"projects/p/secrets/{secret_id}/versions/{len(self.added)}"


def test_generates_a_urlsafe_key_and_adds_one_version() -> None:
    store = _Store()
    name = rotate_mod.rotate_api_key(
        store=store, secret_id="API_KEY", generate=lambda: "GENERATED-API-KEY-0123456789abcdef"
    )
    assert store.added == [("API_KEY", "GENERATED-API-KEY-0123456789abcdef")]
    assert name.endswith("/versions/1")


def test_default_generator_is_a_long_urlsafe_token() -> None:
    assert len(rotate_mod._generate()) == 64
    assert rotate_mod._generate() != rotate_mod._generate()


def test_refuses_the_public_placeholder_before_touching_the_store() -> None:
    store = _Store()
    with pytest.raises(RuntimeError):
        rotate_mod.rotate_api_key(
            store=store, secret_id="API_KEY", generate=lambda: rotate_mod.PLACEHOLDER
        )
    assert store.added == []


def test_refuses_a_degenerate_generated_key() -> None:
    store = _Store()
    with pytest.raises(RuntimeError):
        rotate_mod.rotate_api_key(store=store, secret_id="API_KEY", generate=lambda: "short")
    assert store.added == []


def test_store_failure_leaves_nothing_half_done() -> None:
    class FailingStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def add_version(self, secret_id, value):
            self.attempts += 1
            raise OSError("secret manager down")

    store = FailingStore()
    with pytest.raises(OSError):
        rotate_mod.rotate_api_key(
            store=store, secret_id="API_KEY", generate=lambda: "GENERATED-API-KEY-0123456789abcdef"
        )
    assert store.attempts == 1
    assert store.added == []


def test_main_exits_2_when_configuration_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(SystemExit) as exc:
        rotate_mod.main()
    assert exc.value.code == 2


def test_main_succeeds_and_logs_no_key(monkeypatch, caplog) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("API_KEY_SECRET_ID", "API_KEY")
    monkeypatch.delenv("API_KEY", raising=False)
    key = "GENERATED-API-KEY-0123456789abcdef"
    monkeypatch.setattr(rotate_mod, "SecretStore", lambda project: _Store())
    monkeypatch.setattr(rotate_mod, "_generate", lambda: key)

    with caplog.at_level("DEBUG"):
        with pytest.raises(SystemExit) as exc:
            rotate_mod.main()

    assert exc.value.code == 0
    assert key not in caplog.text
    assert "API key rotation complete" in caplog.text


def test_main_exits_1_on_store_failure_and_logs_no_key(monkeypatch, caplog) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.delenv("API_KEY", raising=False)

    class FailingStore(_Store):
        def add_version(self, secret_id, value):
            raise OSError(f"cannot store {value}")

    monkeypatch.setattr(rotate_mod, "SecretStore", lambda project: FailingStore())
    monkeypatch.setattr(rotate_mod, "_generate", lambda: "GENERATED-API-KEY-0123456789abcdef")

    with caplog.at_level("DEBUG"):
        with pytest.raises(SystemExit) as exc:
            rotate_mod.main()

    assert exc.value.code == 1
    assert "GENERATED-API-KEY-0123456789abcdef" not in caplog.text


def test_secret_store_reads_a_version_without_logging_it(caplog) -> None:
    from agentic_kg.rotate_password import SecretStore

    calls = []
    value = "VALUE-xyz-0123456789"

    def http(url, *, method="GET", headers=None, body=None):
        calls.append((url, method, headers))
        if "metadata" in url:
            return json.dumps({"access_token": "tok"}).encode()
        return json.dumps(
            {"payload": {"data": base64.b64encode(value.encode()).decode()}}
        ).encode()

    with caplog.at_level("DEBUG"):
        result = SecretStore("p", http).access_version("API_KEY")

    assert result == value
    assert calls[-1][0].endswith("/projects/p/secrets/API_KEY/versions/latest:access")
    assert calls[-1][2]["Authorization"] == "Bearer tok"
    assert value not in caplog.text


# -- check Job ----------------------------------------------------------------


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_check_passes_on_200_ok_and_sends_the_header() -> None:
    seen = []

    def http(request, *, timeout):
        seen.append(request)
        return _Response(200, b'{"ok": true}')

    check_mod.check_api_key("https://api.example/", "KEY-0123456789", http=http)
    assert seen[0].full_url == "https://api.example/api/auth/check"
    assert seen[0].get_header("X-api-key") == "KEY-0123456789"


def test_check_fails_on_wrong_status_or_body() -> None:
    def make_http(status: int, body: bytes):
        return lambda request, *, timeout: _Response(status, body)

    for status, body in [(401, b"{}"), (200, b'{"ok": false}'), (200, b"not json")]:
        with pytest.raises(check_mod.CheckFailed):
            check_mod.check_api_key(
                "https://api.example", "KEY-0123456789", http=make_http(status, body)
            )


def test_check_refuses_placeholder_without_a_request() -> None:
    called = []
    with pytest.raises(check_mod.CheckFailed):
        check_mod.check_api_key(
            "https://api.example",
            check_mod.PLACEHOLDER,
            http=lambda request, timeout: called.append(1),
        )
    assert called == []


def test_check_retries_before_failing() -> None:
    attempts = []

    def http(request, *, timeout):
        attempts.append(1)
        raise OSError("not ready")

    with pytest.raises(check_mod.CheckFailed):
        check_mod.check_api_key(
            "https://api.example",
            "KEY-0123456789",
            attempts=3,
            interval=0,
            http=http,
            sleep=lambda _: None,
        )
    assert len(attempts) == 3


def test_check_main_prints_only_pass_fail_and_the_key(monkeypatch, capsys) -> None:
    key = "KEY-0123456789-ultra-secret"
    monkeypatch.setenv("API_URL", "https://api.example")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")

    class _Reader:
        def __init__(self, project: str) -> None:
            pass

        def access_version(self, secret_id: str) -> str:
            return key

    monkeypatch.setattr(check_mod, "SecretStore", _Reader)
    monkeypatch.setattr(check_mod, "check_api_key", lambda *a, **k: None)

    with pytest.raises(SystemExit) as exc:
        check_mod.main()

    out = capsys.readouterr().out
    assert exc.value.code == 0
    assert out.strip() == "API key check: PASS"
    assert key not in out


def test_check_main_fails_closed_without_configuration(monkeypatch, capsys) -> None:
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    with pytest.raises(SystemExit) as exc:
        check_mod.main()
    assert exc.value.code == 1
    assert capsys.readouterr().out.strip() == "API key check: FAIL"
