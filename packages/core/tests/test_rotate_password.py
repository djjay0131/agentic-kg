"""Unit tests for the in-VPC Neo4j password-rotation entrypoint (ADR-0003).

No live Neo4j: the driver factory is injected. The tests pin the contract
that matters — the ALTER uses parameters, a read probe is run with the new
credential, and neither password is ever logged.
"""

from __future__ import annotations

import pytest
from agentic_kg import rotate_password


class _Result:
    def __init__(self, record=None) -> None:
        self._record = record

    def consume(self):
        return self

    def single(self):
        return self._record


class _Session:
    def __init__(self, driver) -> None:
        self._driver = driver

    def run(self, query, **params):
        self._driver.calls.append((query, params))
        if query == rotate_password._READ_PROBE:
            return _Result({"ok": self._driver.probe_value})
        return _Result()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Driver:
    probe_value = 1

    def __init__(self, uri, auth) -> None:
        self.uri = uri
        self.auth = auth
        self.calls: list[tuple[str, dict]] = []

    def verify_connectivity(self) -> None:
        return None

    def session(self):
        return _Session(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Factory:
    def __init__(self) -> None:
        self.drivers: list[_Driver] = []

    def __call__(self, uri, auth):
        driver = _Driver(uri, auth)
        self.drivers.append(driver)
        return driver


def test_rotate_changes_then_verifies_with_new_password() -> None:
    factory = _Factory()
    rotate_password._rotate(
        "bolt://10.0.0.2:7687", "neo4j", "OLD-secret", "NEW-secret", factory
    )

    assert len(factory.drivers) == 2
    assert factory.drivers[0].auth == ("neo4j", "OLD-secret")
    assert factory.drivers[1].auth == ("neo4j", "NEW-secret")

    alter_query, alter_params = factory.drivers[0].calls[0]
    assert alter_query.startswith("ALTER CURRENT USER SET PASSWORD FROM")
    assert alter_params == {
        "currentPassword": "OLD-secret",
        "nextPassword": "NEW-secret",
    }
    # The probe must use the *new* connection.
    assert factory.drivers[1].calls[0][0] == rotate_password._READ_PROBE


def test_rotate_raises_when_probe_is_not_ok() -> None:
    class BadFactory(_Factory):
        def __call__(self, uri, auth):
            driver = _Driver(uri, auth)
            driver.probe_value = 0
            self.drivers.append(driver)
            return driver

    with pytest.raises(RuntimeError):
        rotate_password._rotate(
            "bolt://x:7687", "neo4j", "old", "new", BadFactory()
        )


def test_main_exits_2_when_configuration_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD_NEXT", raising=False)

    with pytest.raises(SystemExit) as exc:
        rotate_password.main()
    assert exc.value.code == 2


def test_main_exits_0_on_success_and_logs_no_password(monkeypatch, caplog) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://10.0.0.2:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "CURRENT-ultra-secret")
    monkeypatch.setenv("NEO4J_PASSWORD_NEXT", "NEXT-ultra-secret")
    monkeypatch.setattr(rotate_password, "_rotate", lambda *a, **k: None)

    with caplog.at_level("DEBUG"):
        with pytest.raises(SystemExit) as exc:
            rotate_password.main()

    assert exc.value.code == 0
    assert "CURRENT-ultra-secret" not in caplog.text
    assert "NEXT-ultra-secret" not in caplog.text


def test_main_exits_1_on_failure_and_logs_no_password(monkeypatch, caplog) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://10.0.0.2:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "CURRENT-ultra-secret")
    monkeypatch.setenv("NEO4J_PASSWORD_NEXT", "NEXT-ultra-secret")

    def boom(*a, **k):
        raise RuntimeError("driver said no")

    monkeypatch.setattr(rotate_password, "_rotate", boom)

    with caplog.at_level("DEBUG"):
        with pytest.raises(SystemExit) as exc:
            rotate_password.main()

    assert exc.value.code == 1
    assert "CURRENT-ultra-secret" not in caplog.text
    assert "NEXT-ultra-secret" not in caplog.text
