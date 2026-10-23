from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL
from sqlmodel import Session, SQLModel, create_engine


class Database:
    def __init__(self, path: Path):
        self._engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(path)),
            connect_args={"check_same_thread": False},
            hide_parameters=True,
        )

    def initialize(self):
        SQLModel.metadata.create_all(self._engine)

    @contextmanager
    def session(self):
        with Session(self._engine, expire_on_commit=False) as session:
            with session.begin():
                yield session

    def close(self):
        self._engine.dispose()
