"""DB 接続（SQLAlchemy 2.0）。PoC は SQLite。"""
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
# SQLite は並行（スケジューラ書込×API読取）でロックしやすいので timeout を持たせる
_connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        # WAL: 書込中も読取可 / busy_timeout: ロック時は即エラーにせず待つ
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        # FK整合をPostgreSQLと同等に強制（SQLiteは既定OFF。孤児行の混入防止 — #30 対抗レビュー[medium]）
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
