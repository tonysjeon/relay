from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import Settings
from sqlalchemy import create_engine, text


def pytest_addoption(parser):
    parser.addoption(
        "--integration", action="store_true", help="Run live service checks"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        return
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(
                pytest.mark.skip(reason="Pass --integration for live services")
            )


@pytest.fixture(scope="module")
def database():
    """Use a dedicated database; never migrate or clear the application database."""
    admin = create_engine(Settings().database_url, isolation_level="AUTOCOMMIT")
    name = f"relay_test_{uuid4().hex}"
    engine = create_engine(admin.url.set(database=name))
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
