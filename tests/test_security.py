import base64
import logging

import pytest
from fastapi.testclient import TestClient

from wikichat.index import SearchIndex
from wikichat.redact import HIDDEN, redact
from wikichat.security import SecretFilter, is_loopback
from wikichat.server import create_app


# --------------------------------------------------------------- redaction
@pytest.mark.parametrize("text", [
    "Password: Tr0ub4dor&3",
    "WLAN-Passwort = sommer2026!",
    "| Kennwort || hunter22",
    "The PIN is 4711",
    "Türcode: 8812#",
    "api_key=abc123def456",
    "Key sk-proj-AbCdEfGhIjKlMnOpQrStUv123",
    "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "clone https://hamada:s3cret@gitlab.example.org/repo.git",
    "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
])
def test_secrets_are_hidden(text):
    out, n = redact(text)
    assert n >= 1 and HIDDEN in out
    for secret in ("Tr0ub4dor", "sommer2026", "hunter22", "4711", "8812", "abc123def456",
                   "sk-proj", "ghp_", "s3cret", "\nabc\n"):
        assert secret not in out


def test_normal_text_is_untouched():
    text = "Ask Simone for a password reset. The password policy needs 12 characters."
    assert redact(text) == (text, 0)


def test_index_has_no_passwords_or_excluded_pages(settings):
    idx = SearchIndex.build(settings)
    blob = "\n".join(c.text for c in idx.chunks)
    assert "Tr0ub4dor" not in blob and HIDDEN in blob
    assert all(c.page_title != "Admin secrets" for c in idx.chunks)
    assert idx.meta["hidden_values"] >= 1 and idx.meta["excluded_pages"] == 1


# ------------------------------------------------------------------ server
def _client(settings, **changes):
    s = settings.model_copy(update=changes)
    SearchIndex.build(s)
    return TestClient(create_app(s))


def test_security_headers_and_no_api_docs(settings):
    c = _client(settings)
    r = c.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_password_protects_everything_but_healthz(settings):
    c = _client(settings, access_password="correct horse battery")
    assert c.get("/").status_code == 401
    assert c.get("/api/status").status_code == 401
    assert c.get("/healthz").status_code == 200
    bad = base64.b64encode(b"x:wrong").decode()
    good = base64.b64encode(b"anyone:correct horse battery").decode()
    assert c.get("/api/status", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    assert c.get("/api/status", headers={"Authorization": f"Basic {good}"}).status_code == 200


def test_rate_limit(settings):
    c = _client(settings, rate_limit_per_minute=3)
    codes = [c.get("/api/search", params={"q": "gpu"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[-1] == 429
    assert c.get("/api/status").status_code == 200  # only chat and search are limited


def test_request_size_limits(settings):
    c = _client(settings)
    assert c.post("/api/chat", json={"message": "x" * 5000}).status_code == 422
    too_long = [{"role": "user", "content": "hi"}] * 25
    assert c.post("/api/chat", json={"message": "hi", "history": too_long}).status_code == 422
    assert c.post("/api/chat", json={"message": "hi", "history": [{"role": "system", "content": "x"}]}).status_code == 422


def test_llm_errors_do_not_leak_details(settings):
    c = _client(settings, llm_provider="openai", llm_base_url="http://127.0.0.1:9/v1")
    r = c.post("/api/chat", json={"message": "gpu access"})
    assert "event: error" in r.text and "127.0.0.1" not in r.text


def test_status_has_branding_and_no_paths(settings):
    c = _client(settings, org_name="DICE", org_url="https://dice-research.org", suggestions="A?|B?")
    s = c.get("/api/status").json()
    assert s["org_name"] == "DICE" and s["suggestions"] == ["A?", "B?"]
    assert str(settings.data_dir) not in str(s)


def test_org_logo_is_served(settings, tmp_path):
    logo = tmp_path / "org.svg"
    logo.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    c = _client(settings, org_logo_file=str(logo))
    assert c.get("/api/status").json()["org_logo"] == "/brand/org-logo"
    assert c.get("/brand/org-logo").headers["content-type"].startswith("image/svg")


# ------------------------------------------------------------------ helpers
def test_secret_filter_masks_values():
    record = logging.LogRecord("x", logging.ERROR, "", 0, "cookie was %s", ("abc123secret",), None)
    SecretFilter(["abc123secret"]).filter(record)
    assert record.getMessage() == "cookie was ***"


def test_loopback_detection():
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("10.0.0.5")


def test_serve_refuses_open_network_without_password(monkeypatch):
    from wikichat import cli, vault
    from wikichat.config import Settings

    monkeypatch.setattr(vault, "get", lambda name: "")
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
    assert cli.open_bind_problem("0.0.0.0")
    assert cli.open_bind_problem("127.0.0.1") is None
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, access_password="long enough pw"))
    assert cli.open_bind_problem("0.0.0.0") is None
