import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import DATABASE_URL

logger = logging.getLogger(__name__)

_is_sqlite = DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = dict(pool_pre_ping=True)

if _is_sqlite:
    _engine_kwargs.update(
        connect_args={"check_same_thread": False, "timeout": 30},
    )
else:
    _engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        connect_args={"charset": "utf8mb4"},
    )

engine = create_engine(DATABASE_URL, **_engine_kwargs)

if _is_sqlite:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
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


def age_in_hours(column):
    """Return an SQLAlchemy expression for hours since *column* until now.

    Works on both SQLite (julianday) and MySQL (TIMESTAMPDIFF).
    """
    from sqlalchemy import func
    if _is_sqlite:
        return (func.julianday(func.datetime("now")) - func.julianday(column)) * 24.0
    return func.timestampdiff(text("HOUR"), column, func.now())


def _table_has_column(insp, table: str, column: str) -> bool:
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
        return column in cols
    except Exception:
        return False


def init_db():
    from app.models import paper, agent, score, comment, user, notification, bookmark, recommendation, bookmark_folder, daily_summary  # noqa: F401

    Base.metadata.create_all(bind=engine)

    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    migrations: list[tuple[str, str, str]] = [
        ("papers", "uploaded_by", "ALTER TABLE papers ADD COLUMN uploaded_by INTEGER DEFAULT NULL"),
        ("comments", "user_id", "ALTER TABLE comments ADD COLUMN user_id INTEGER DEFAULT NULL"),
        ("notifications", "recipient_user_id", "ALTER TABLE notifications ADD COLUMN recipient_user_id INTEGER DEFAULT NULL"),
        ("notifications", "actor_user_id", "ALTER TABLE notifications ADD COLUMN actor_user_id INTEGER DEFAULT NULL"),
        ("users", "avatar", "ALTER TABLE users ADD COLUMN avatar VARCHAR(256) DEFAULT NULL"),
        ("users", "bio", "ALTER TABLE users ADD COLUMN bio TEXT DEFAULT NULL"),
        ("users", "interests", "ALTER TABLE users ADD COLUMN interests JSON DEFAULT NULL"),
        ("bookmarks", "folder_id", "ALTER TABLE bookmarks ADD COLUMN folder_id INTEGER DEFAULT NULL"),
    ]

    with engine.begin() as conn:
        for table, col, sql in migrations:
            if table not in existing_tables:
                continue
            if not _table_has_column(insp, table, col):
                try:
                    conn.execute(text(sql))
                    logger.info("Added column %s.%s", table, col)
                except Exception as e:
                    logger.debug("Column %s.%s migration skipped: %s", table, col, e)

    if not _is_sqlite:
        with engine.begin() as conn:
            for table in existing_tables:
                try:
                    conn.execute(text(
                        f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    ))
                    logger.info("Converted %s to utf8mb4", table)
                except Exception as e:
                    logger.debug("utf8mb4 conversion skipped for %s: %s", table, e)

    logger.info("Database initialised (dialect=%s)", engine.dialect.name)
