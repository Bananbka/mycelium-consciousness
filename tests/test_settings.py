"""Per-environment configuration and the production safety rails."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from api.main import app
from shared import settings as settings_module
from shared.settings import ConfigurationError, Settings


def _values(**overrides) -> dict:
    base = {
        "app_env": "sandbox",
        "postgres_user": "u",
        "postgres_password": "p@ss/word%",
        "postgres_host": "db",
        "postgres_db": "clone_memory_sandbox",
        "celery_broker_url": "redis://redis:6379/0",
        "celery_result_backend": "redis://redis:6379/0",
        "minio_endpoint": "http://minio:9000",
        "minio_access_key": "access-key-value",
        "minio_secret_key": "secret-key-value",
        "minio_bucket": "memory-backups-sandbox",
        "jwt_secret_key": "this-is-a-fixture-secret-not-a-real-one-" * 2,
    }
    base.update(overrides)
    return base


def _production(**overrides) -> dict:
    values = _values(
        app_env="production",
        postgres_db="clone_memory_production",
        minio_bucket="memory-backups-production",
    )
    values.update(overrides)
    return values


def _build(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_sandbox_and_production_can_use_separate_databases():
    sandbox = _build(**_values())
    production = _build(**_production())

    assert sandbox.postgres_db != production.postgres_db
    assert sandbox.database_url.endswith("/clone_memory_sandbox")
    assert production.database_url.endswith("/clone_memory_production")


def test_database_url_escapes_special_characters_in_the_password():
    url = _build(**_values()).database_url
    assert "p%40ss%2Fword%25" in url


def test_debug_defaults_to_off():
    assert _build(**_values()).debug is False


def test_debug_is_allowed_in_sandbox():
    assert _build(**_values(debug=True)).debug is True


def test_production_refuses_debug():
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        _build(**_production(debug=True))


def test_production_refuses_reload_and_insecure_secret_override():
    with pytest.raises(ValidationError, match="API_RELOAD"):
        _build(**_production(api_reload=True))
    with pytest.raises(ValidationError, match="ALLOW_INSECURE_JWT_SECRET"):
        _build(**_production(allow_insecure_jwt_secret=True))


def test_production_refuses_change_me_placeholders():
    with pytest.raises(ValidationError, match="CHANGE_ME"):
        _build(**_production(postgres_password="CHANGE_ME_db_password"))


@pytest.mark.parametrize("field", ["postgres_db", "minio_bucket"])
def test_production_refuses_sandbox_or_test_resources(field):
    with pytest.raises(ValidationError, match="production must use its own"):
        _build(**_production(**{field: "something-sandbox"}))


@pytest.mark.parametrize("field", ["postgres_db", "minio_bucket"])
def test_sandbox_refuses_production_resources(field):
    with pytest.raises(ValidationError, match="never point at production"):
        _build(**_values(**{field: "clone_memory_production"}))


@pytest.mark.parametrize(
    "missing",
    [
        "postgres_password",
        "postgres_db",
        "minio_access_key",
        "minio_secret_key",
        "jwt_secret_key",
    ],
)
def test_secrets_have_no_default(missing, monkeypatch):
    # The suite's own environment supplies these; hide it so only the model's
    # defaults are under test.
    monkeypatch.delenv(missing.upper(), raising=False)
    values = _values()
    del values[missing]
    with pytest.raises(ValidationError):
        _build(**values)


def test_get_settings_requires_a_known_app_env(monkeypatch):
    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "staging")
    try:
        with pytest.raises(ConfigurationError, match="APP_ENV"):
            settings_module.get_settings()
    finally:
        settings_module.get_settings.cache_clear()


def test_get_settings_reads_the_env_specific_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.sandbox"
    env_file.write_text(
        "\n".join(f"{k.upper()}={v}" for k, v in _values().items() if k != "app_env")
        + "\nDEBUG=true\n"
    )
    for key in _values():
        monkeypatch.delenv(key.upper(), raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.setenv("APP_ENV", "sandbox")
    monkeypatch.setenv("ENV_FILE", str(env_file))

    settings_module.get_settings.cache_clear()
    try:
        loaded = settings_module.get_settings()
    finally:
        settings_module.get_settings.cache_clear()

    assert loaded.app_env == "sandbox"
    assert loaded.debug is True
    assert loaded.postgres_db == "clone_memory_sandbox"


async def test_unhandled_error_returns_opaque_500_without_a_traceback(caplog):
    async def boom():
        raise RuntimeError("secret-internal-detail /srv/app/models.py")

    app.add_api_route("/__boom", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            with caplog.at_level("ERROR"):
                response = await http.get("/__boom")
    finally:
        app.router.routes.pop()

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal server error"
    assert body["error_id"]
    assert "secret-internal-detail" not in response.text
    assert "Traceback" not in response.text
    # The detail is kept where operators can find it, keyed by the same id.
    assert any(body["error_id"] in record.getMessage() for record in caplog.records)


def test_interactive_docs_are_enabled_outside_production():
    # The suite runs as APP_ENV=test; production disables these.
    assert app.docs_url == "/docs"
