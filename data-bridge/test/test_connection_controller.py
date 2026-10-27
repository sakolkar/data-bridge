from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import (
    Column,
    LargeBinary,
    MetaData,
    String,
    Table,
    URL,
    create_engine,
    inspect,
)
from sqlalchemy import select, text

from controllers.connection import ConnectionController
from db.db import Database
from db.models import ConnectionRecord
from db import fields
from exceptions import (
    ConnectionNotFoundError,
    ConnectionStoreError,
    DuplicateConnectionError,
)


@pytest.fixture
def database(sqlite_path: Path) -> Iterator[Database]:
    sqlite_path.parent.mkdir()
    with closing(Database(sqlite_path)) as database:
        database.initialize()
        yield database


@pytest.fixture
def controller(secret_key: str, database: Database) -> ConnectionController:
    controller = ConnectionController(database.session)
    controller.initialize()
    return controller


@pytest.mark.parametrize(
    "fields",
    [{"type": " "}, {"settings": None}, {"name": "x" * 129}, {"extra": "value"}],
)
def test_record_validates_configuration(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ConnectionRecord.model_validate(
            {"name": "source", "type": "custom", "settings": "", **fields}
        )


def test_configuration_survives_restart(
    controller: ConnectionController, database: Database, secret_key: str, sqlite_path: Path
) -> None:
    request = ConnectionRecord(
        name="source", type="custom", settings="password=private-value;port=22"
    )
    assert "private-value" not in repr(request)
    controller.create(request)
    database.close()

    with closing(Database(sqlite_path)) as restarted_database:
        restarted_database.initialize()
        restarted = ConnectionController(restarted_database.session)
        restarted.initialize()
        configuration = restarted.get("source")
        assert isinstance(configuration, ConnectionRecord)
        assert configuration.name == "source"
        assert configuration.type == "custom"
        assert configuration.settings == request.settings
        assert "private-value" not in repr(configuration)


def test_settings_are_encrypted_at_rest(
    controller: ConnectionController, secret_key: str, sqlite_path: Path
) -> None:
    request = ConnectionRecord(
        name="source", type="custom", settings="password=private-value"
    )
    controller.create(request)

    engine = create_engine(URL.create("sqlite", database=str(sqlite_path)))
    try:
        with engine.connect() as database:
            encrypted = database.execute(
                text("SELECT settings FROM connection")
            ).scalar_one()
        assert isinstance(encrypted, str)
        assert b"private-value" not in sqlite_path.read_bytes()
        assert b"private-value" in Fernet(secret_key.encode("ascii")).decrypt(
            encrypted.encode("utf-8")
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "settings", ["", "password=private-value", "line one\nline two \u00e9"]
)
def test_model_transparently_round_trips_settings(
    database: Database, settings: str
) -> None:
    with database.session() as session:
        session.add(ConnectionRecord(name="source", type="custom", settings=settings))
    with database.session() as session:
        record = session.get(ConnectionRecord, "source")
        assert record is not None
        assert record.settings == settings
        assert settings == "" or settings not in repr(record)


def test_duplicate_names_do_not_replace_existing_configuration(
    controller: ConnectionController,
) -> None:
    original = ConnectionRecord(name="source", type="custom", settings="original")
    controller.create(original)
    with pytest.raises(DuplicateConnectionError):
        controller.create(ConnectionRecord(name="source", type="other", settings=""))

    assert controller.get("source").settings == original.settings
    controller.create(
        ConnectionRecord(name="Source", type=original.type, settings=original.settings)
    )
    assert [item.name for item in controller.list()] == ["Source", "source"]


def test_replacement_and_deletion_are_persistent(
    controller: ConnectionController, secret_key: str, sqlite_path: Path
) -> None:
    controller.create(ConnectionRecord(name="source", type="first", settings="old"))
    replacement = ConnectionRecord(name="source", type="second", settings="new")
    controller.replace(replacement)

    with closing(Database(sqlite_path)) as restarted_database:
        restarted = ConnectionController(restarted_database.session)
        assert restarted.get("source").type == "second"
        assert restarted.get("source").settings == replacement.settings
        restarted.delete("source")
    assert controller.list() == []
    with pytest.raises(ConnectionNotFoundError):
        controller.get("source")


def test_missing_connections_are_distinguishable(controller: ConnectionController) -> None:
    with pytest.raises(ConnectionNotFoundError):
        controller.get("missing")
    with pytest.raises(ConnectionNotFoundError):
        controller.get_type("missing")
    with pytest.raises(ConnectionNotFoundError):
        controller.replace(ConnectionRecord(name="missing", type="custom", settings=""))
    with pytest.raises(ConnectionNotFoundError):
        controller.delete("missing")


@pytest.mark.parametrize("invalid_token", [False, True])
def test_decryption_failure_raises_store_error(
    controller: ConnectionController,
    sqlite_path: Path,
    invalid_token: bool,
) -> None:
    controller.create(
        ConnectionRecord(
            name="source", type="custom", settings="password=private-value"
        ),
    )
    if invalid_token:
        engine = create_engine(URL.create("sqlite", database=str(sqlite_path)))
        try:
            with engine.begin() as database:
                database.execute(
                    text("UPDATE connection SET settings = :settings"),
                    {"settings": "invalid-ciphertext"},
                )
        finally:
            engine.dispose()

    if not invalid_token:
        fields.configure(Fernet.generate_key().decode("ascii"))
    with closing(Database(sqlite_path)) as restarted_database:
        restarted = ConnectionController(restarted_database.session)
        with pytest.raises(ConnectionStoreError, match="decrypt"):
            restarted.get("source")
        assert restarted.get_type("source") == "custom"


def test_storage_failure_is_not_an_empty_list(
    controller: ConnectionController, sqlite_path: Path
) -> None:
    engine = create_engine(URL.create("sqlite", database=str(sqlite_path)))
    try:
        ConnectionRecord.__table__.drop(engine)
    finally:
        engine.dispose()
    with pytest.raises(ConnectionStoreError, match="unavailable"):
        controller.list()


def test_initialize_preserves_unrelated_tables(
    secret_key: str, sqlite_path: Path
) -> None:
    sqlite_path.parent.mkdir()
    engine = create_engine(URL.create("sqlite", database=str(sqlite_path)))
    metadata = MetaData()
    unrelated = Table("unrelated", metadata, Column("value", String))
    try:
        metadata.create_all(engine)
        with engine.begin() as database:
            database.execute(unrelated.insert().values(value="existing data"))
        with closing(Database(sqlite_path)) as storage:
            storage.initialize()
            controller = ConnectionController(storage.session)
            controller.initialize()
            storage.initialize()
            controller.initialize()
        with engine.connect() as database:
            assert (
                database.execute(select(unrelated.c.value)).scalar_one()
                == "existing data"
            )
        assert "connection" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_incompatible_schema_is_not_silently_accepted(
    secret_key: str, sqlite_path: Path
) -> None:
    sqlite_path.parent.mkdir()
    engine = create_engine(URL.create("sqlite", database=str(sqlite_path)))
    metadata = MetaData()
    Table("connection", metadata, Column("settings", LargeBinary))
    try:
        metadata.create_all(engine)
        with closing(Database(sqlite_path)) as storage:
            storage.initialize()
            controller = ConnectionController(storage.session)
            with pytest.raises(ConnectionStoreError):
                controller.initialize()
    finally:
        engine.dispose()


def test_existing_encrypted_text_database_is_compatible(
    secret_key: str, sqlite_path: Path
) -> None:
    sqlite_path.parent.mkdir()
    engine = create_engine(URL.create("sqlite", database=str(sqlite_path)))
    metadata = MetaData()
    legacy = Table(
        "connection",
        metadata,
        Column("name", String(128, collation="BINARY"), primary_key=True),
        Column("type", String(64), nullable=False),
        Column("settings", String, nullable=False),
    )
    encrypted = (
        Fernet(secret_key.encode("ascii"))
        .encrypt(b"password=private-value")
        .decode("utf-8")
    )
    try:
        metadata.create_all(engine)
        with engine.begin() as database:
            database.execute(
                legacy.insert().values(name="source", type="custom", settings=encrypted)
            )
        with closing(Database(sqlite_path)) as storage:
            storage.initialize()
            controller = ConnectionController(storage.session)
            controller.initialize()
            assert controller.get("source").settings == "password=private-value"
            listed = controller.list()[0]
            assert listed.name == "source"
            assert listed.type == "custom"
            assert listed.settings == controller.get("source").settings
            controller.replace(
                ConnectionRecord(name="source", type="updated", settings="port=22")
            )
        with engine.connect() as database:
            row = database.execute(select(legacy)).one()
            assert row.type == "updated"
            assert (
                Fernet(secret_key.encode("ascii")).decrypt(row.settings.encode("utf-8"))
                == b"port=22"
            )
    finally:
        engine.dispose()
