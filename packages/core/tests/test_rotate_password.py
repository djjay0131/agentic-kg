"""Unit tests for the in-VPC Neo4j password-rotation entrypoint (ADR-0006).

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


# -- ADR-0007: in-GCP generation ------------------------------------------------


class _Store:
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []

    def add_version(self, secret_id: str, value: str) -> str:
        self.added.append((secret_id, value))
        return f"projects/p/secrets/{secret_id}/versions/{len(self.added)}"


class _AuthFactory(_Factory):
    """Only ``valid`` authenticates; ALTER switches the valid password."""

    def __init__(self, valid: str) -> None:
        super().__init__()
        self.valid = valid

    def __call__(self, uri, auth):
        factory = self
        driver = _Driver(uri, auth)

        def verify() -> None:
            if auth[1] != factory.valid:
                from neo4j.exceptions import AuthError

                raise AuthError("bad credentials")

        driver.verify_connectivity = verify
        original_session = driver.session

        def session():
            s = original_session()
            run = s.run

            def run_and_apply(query, **params):
                if query == rotate_password._ALTER_PASSWORD:
                    factory.valid = params["nextPassword"]
                return run(query, **params)

            s.run = run_and_apply
            return s

        driver.session = session
        self.drivers.append(driver)
        return driver


def _gcp(factory, store, current, staged=None):
    rotate_password.rotate_in_gcp(
        uri="bolt://10.0.0.2:7687", username="neo4j", current_password=current,
        staged_password=staged, store=store, password_secret_id="NEO4J_PASSWORD",
        next_secret_id="NEO4J_PASSWORD_NEXT", driver_factory=factory,
        generate=lambda: "GENERATED-in-gcp",
    )


def test_gcp_mode_generates_stages_first_then_stores_after_verify() -> None:
    factory, store = _AuthFactory("OLD"), _Store()
    _gcp(factory, store, "OLD", staged=rotate_password.PLACEHOLDER)
    assert store.added == [
        ("NEO4J_PASSWORD_NEXT", "GENERATED-in-gcp"),  # staged before ALTER
        ("NEO4J_PASSWORD", "GENERATED-in-gcp"),  # promoted after the probe
    ]
    assert factory.valid == "GENERATED-in-gcp"


def test_gcp_mode_recovers_an_interrupted_rotation() -> None:
    # ALTER happened last time but NEO4J_PASSWORD was never updated.
    factory, store = _AuthFactory("STAGED"), _Store()
    _gcp(factory, store, "OLD", staged="STAGED")
    assert store.added[0] == ("NEO4J_PASSWORD", "STAGED")
    assert store.added[-1] == ("NEO4J_PASSWORD", "GENERATED-in-gcp")
    assert factory.valid == "GENERATED-in-gcp"


def test_gcp_mode_refuses_when_nothing_authenticates() -> None:
    factory, store = _AuthFactory("SOMETHING-ELSE"), _Store()
    with pytest.raises(RuntimeError):
        _gcp(factory, store, "OLD", staged=rotate_password.PLACEHOLDER)
    assert store.added == []


def test_gcp_mode_never_stages_if_the_store_write_fails() -> None:
    factory = _AuthFactory("OLD")

    class FailingStore(_Store):
        def add_version(self, secret_id, value):
            raise OSError("secret manager down")

    with pytest.raises(OSError):
        _gcp(factory, FailingStore(), "OLD")
    assert factory.valid == "OLD"  # Neo4j untouched when staging fails


def test_secret_store_posts_base64_and_logs_only_the_version_name(caplog) -> None:
    import base64
    import json

    calls = []

    def http(url, *, method="GET", headers=None, body=None):
        calls.append((url, method, headers, body))
        if "metadata" in url:
            return json.dumps({"access_token": "tok"}).encode()
        return json.dumps({"name": "projects/p/secrets/S/versions/7"}).encode()

    with caplog.at_level("DEBUG"):
        name = rotate_password.SecretStore("p", http).add_version("S", "VALUE-xyz")
    assert name.endswith("/versions/7")
    url, method, headers, body = calls[-1]
    assert url.endswith("/projects/p/secrets/S:addVersion") and method == "POST"
    assert headers["Authorization"] == "Bearer tok"
    assert base64.b64decode(json.loads(body)["payload"]["data"]) == b"VALUE-xyz"
    assert "VALUE-xyz" not in caplog.text


def test_main_uses_gcp_mode_when_secret_ids_are_set(monkeypatch, caplog) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://10.0.0.2:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "CURRENT-ultra-secret")
    monkeypatch.delenv("NEO4J_PASSWORD_NEXT", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("NEO4J_PASSWORD_SECRET_ID", "NEO4J_PASSWORD")
    monkeypatch.setenv("NEO4J_PASSWORD_NEXT_SECRET_ID", "NEO4J_PASSWORD_NEXT")
    seen = {}
    monkeypatch.setattr(rotate_password, "rotate_in_gcp", lambda **k: seen.update(k))
    with caplog.at_level("DEBUG"):
        with pytest.raises(SystemExit) as exc:
            rotate_password.main()
    assert exc.value.code == 0
    assert seen["password_secret_id"] == "NEO4J_PASSWORD" and seen["staged_password"] is None
    assert "CURRENT-ultra-secret" not in caplog.text
