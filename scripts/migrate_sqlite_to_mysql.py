#!/usr/bin/env python3
"""
SQLite → MySQL 一次性数据迁移脚本。

用法:
  python scripts/migrate_sqlite_to_mysql.py [--sqlite path/to/paper_pub.db]

默认读取项目根目录下的 paper_pub.db，写入 .env 中配置的 MySQL 实例。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import DATABASE_URL, SQLITE_DB_PATH  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Migrate PaperPub data from SQLite to MySQL")
    parser.add_argument("--sqlite", default=SQLITE_DB_PATH, help="Path to SQLite database file")
    parser.add_argument("--batch-size", type=int, default=500, help="Rows per INSERT batch")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        print(f"SQLite database not found: {sqlite_path}")
        sys.exit(1)

    if DATABASE_URL.startswith("sqlite"):
        print("DATABASE_URL points to SQLite — set it to a MySQL URL in .env first.")
        sys.exit(1)

    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker

    sqlite_url = f"sqlite:///{sqlite_path}"
    src_engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    dst_engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        pool_pre_ping=True,
    )

    from app.database import Base
    from app.models import paper, agent, score, comment, user, notification, bookmark, bookmark_folder, daily_summary  # noqa: F811,F401
    from app.models.recommendation import DailyRecommendation  # noqa: F401

    print("Creating tables in MySQL...")
    Base.metadata.create_all(bind=dst_engine)

    SrcSession = sessionmaker(bind=src_engine)
    DstSession = sessionmaker(bind=dst_engine)

    table_order = [
        "users",
        "papers",
        "agents",
        "scores",
        "comments",
        "notifications",
        "bookmark_folders",
        "bookmarks",
        "daily_recommendations",
        "daily_summaries",
    ]

    src_insp = inspect(src_engine)
    src_tables = set(src_insp.get_table_names())

    dst_insp = inspect(dst_engine)
    dst_tables = set(dst_insp.get_table_names())

    with dst_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

    for table_name in table_order:
        if table_name not in src_tables:
            print(f"  [skip] {table_name} — not in SQLite")
            continue
        if table_name not in dst_tables:
            print(f"  [skip] {table_name} — not in MySQL (table missing)")
            continue

        src_cols = {c["name"] for c in src_insp.get_columns(table_name)}
        dst_cols = {c["name"] for c in dst_insp.get_columns(table_name)}
        common_cols = sorted(src_cols & dst_cols)

        if not common_cols:
            print(f"  [skip] {table_name} — no common columns")
            continue

        with src_engine.connect() as src_conn:
            col_list = ", ".join(common_cols)
            rows = src_conn.execute(text(f"SELECT {col_list} FROM {table_name}")).fetchall()

        total = len(rows)
        if total == 0:
            print(f"  [skip] {table_name} — 0 rows")
            continue

        print(f"  Migrating {table_name}: {total} rows ({len(common_cols)} columns) ... ", end="", flush=True)

        col_placeholders = ", ".join(f":{c}" for c in common_cols)
        col_names = ", ".join(f"`{c}`" for c in common_cols)
        insert_sql = text(f"INSERT INTO `{table_name}` ({col_names}) VALUES ({col_placeholders})")

        dst_col_info = {c["name"]: str(c["type"]).upper() for c in dst_insp.get_columns(table_name)}
        json_cols = {c for c in common_cols if "JSON" in dst_col_info.get(c, "")}
        dt_cols = {c for c in common_cols if any(t in dst_col_info.get(c, "") for t in ("DATE", "TIME", "TIMESTAMP"))}

        with dst_engine.begin() as dst_conn:
            dst_conn.execute(text(f"DELETE FROM `{table_name}`"))

            for i in range(0, total, args.batch_size):
                batch = rows[i:i + args.batch_size]
                row_dicts = []
                for row in batch:
                    d = dict(zip(common_cols, row))
                    for k, v in d.items():
                        if isinstance(v, (list, dict)):
                            d[k] = json.dumps(v, ensure_ascii=False)
                        elif k in json_cols:
                            if v is None:
                                pass
                            elif isinstance(v, str):
                                v = v.strip()
                                if not v or v in ('""', "''", "None"):
                                    d[k] = None
                                else:
                                    try:
                                        json.loads(v)
                                    except (json.JSONDecodeError, ValueError):
                                        d[k] = None
                        elif k in dt_cols:
                            if isinstance(v, str) and not v.strip():
                                d[k] = "1970-01-01 00:00:00"
                            elif v is None and "created_at" in k:
                                d[k] = "1970-01-01 00:00:00"
                    row_dicts.append(d)
                dst_conn.execute(insert_sql, row_dicts)

        with dst_engine.connect() as dst_conn:
            count = dst_conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()

        status = "OK" if count == total else f"MISMATCH ({count}/{total})"
        print(status)

    with dst_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    print("\nMigration complete!")

    print("\n--- Verification ---")
    with dst_engine.connect() as conn:
        for table_name in table_order:
            if table_name not in dst_tables:
                continue
            count = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()
            print(f"  {table_name}: {count} rows")


if __name__ == "__main__":
    main()
