from app.core.config import Settings


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://custom:secret@db:5432/custom")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/2")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://custom:secret@db:5432/custom"
    assert settings.redis_url == "redis://cache:6379/2"
