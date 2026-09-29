"""Engine and session factories for the configured SQLite database."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.database.models import Base


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {})
    if sqlite:
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
    from app.database.documents import init_document_fts
    init_document_fts(engine)
