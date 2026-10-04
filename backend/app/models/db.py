from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.models.tables import Base, User

_engine = None
SessionLocal: sessionmaker[Session] | None = None


def configure_database(settings: Settings):
    global _engine, SessionLocal
    url = settings.resolved_database_url()
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    _engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if url.startswith("sqlite"):
        _enable_sqlite(_engine)
    SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def _enable_sqlite(engine) -> None:
    if getattr(engine, "_beo_sqlite", False):
        return
    engine._beo_sqlite = True

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def database_ready() -> bool:
    if _engine is None:
        return False
    try:
        with _engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


def init_db(settings: Settings) -> None:
    if _engine is None or SessionLocal is None:
        configure_database(settings)
    if settings.auto_create_tables:
        Base.metadata.create_all(_engine)
    assert SessionLocal is not None
    with SessionLocal() as session:
        user = session.get(User, settings.dev_user_id)
        if user is None and not settings.auth_required:
            opening = Decimal(settings.paper_account_balance)
            session.add(
                User(
                    id=settings.dev_user_id,
                    email="local@beo-trade",
                    paper_cash=opening,
                    paper_starting_cash=opening,
                )
            )
            session.commit()


def session_scope() -> Session:
    if SessionLocal is None:
        raise RuntimeError("database is not configured")
    return SessionLocal()
