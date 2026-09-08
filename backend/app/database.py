from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

_is_sqlite = "sqlite" in settings.database_url

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    echo=False,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        """Tune SQLite for concurrent access from backend + multiple worker procs.

        - WAL: readers and one writer can proceed concurrently; greatly reduces
          "database is locked" under parallel evaluation writes.
        - busy_timeout: wait up to 30s on a lock instead of raising OperationalError.
        - synchronous=NORMAL: safe under WAL and faster for the write-heavy eval path.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
