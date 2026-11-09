from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text

from app.database.connection import engine


@contextmanager
def postgres_advisory_lock(lock_key: int) -> Iterator[bool]:
    connection = engine.connect()
    acquired = False
    try:
        if connection.dialect.name != "postgresql":
            yield True
            return

        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar_one()
        )
        connection.commit()
        yield acquired
    finally:
        try:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": lock_key},
                    )
                    connection.commit()
                except Exception:
                    connection.invalidate()
                    raise
        finally:
            connection.close()
