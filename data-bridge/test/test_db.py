from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db.db import Database


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    with closing(Database(tmp_path / "db.sqlite")) as database:
        with database.session() as session:
            session.execute(text("CREATE TABLE item (value TEXT NOT NULL)"))
        yield database


def test_sessions_commit_changes(database: Database) -> None:
    with database.session() as session:
        session.execute(text("INSERT INTO item VALUES ('saved')"))

    with database.session() as session:
        assert session.execute(text("SELECT value FROM item")).scalar_one() == "saved"


@pytest.mark.parametrize("database_error", [False, True])
def test_failed_sessions_roll_back_and_remain_usable(
    database: Database, database_error: bool
) -> None:
    error = SQLAlchemyError if database_error else ValueError
    with pytest.raises(error):
        with database.session() as session:
            session.execute(text("INSERT INTO item VALUES ('discarded')"))
            if database_error:
                session.execute(text("INSERT INTO item VALUES (NULL)"))
            else:
                raise ValueError("Abort transaction.")

    with database.session() as session:
        assert session.execute(text("SELECT count(*) FROM item")).scalar_one() == 0
        session.execute(text("INSERT INTO item VALUES ('saved')"))

    with database.session() as session:
        assert session.execute(text("SELECT value FROM item")).scalar_one() == "saved"
