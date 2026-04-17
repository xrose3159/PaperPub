"""数据库迁移：给 comments 表添加 likes / dislikes 字段。

用法:
    cd PaperPub
    python -m scripts.migrate_add_likes
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import DATABASE_URL

db_path = DATABASE_URL.replace("sqlite:///", "")


def migrate():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(comments)")
    columns = {row[1] for row in cursor.fetchall()}

    added = []
    if "likes" not in columns:
        cursor.execute("ALTER TABLE comments ADD COLUMN likes INTEGER NOT NULL DEFAULT 0")
        added.append("likes")
    if "dislikes" not in columns:
        cursor.execute("ALTER TABLE comments ADD COLUMN dislikes INTEGER NOT NULL DEFAULT 0")
        added.append("dislikes")

    conn.commit()
    conn.close()

    if added:
        print(f"迁移完成，新增字段: {', '.join(added)}")
    else:
        print("字段已存在，无需迁移")


if __name__ == "__main__":
    migrate()
