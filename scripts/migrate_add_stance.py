"""数据库迁移：给 comments 表添加 stance 字段。"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import DATABASE_URL

db_path = DATABASE_URL.replace("sqlite:///", "")


def migrate():
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(comments)")
    cols = {r[1] for r in cur.fetchall()}
    if "stance" not in cols:
        cur.execute("ALTER TABLE comments ADD COLUMN stance VARCHAR(16) DEFAULT 'medium'")
        conn.commit()
        print("迁移完成，新增字段: comments.stance")
    else:
        print("stance 字段已存在，无需迁移")
    conn.close()


if __name__ == "__main__":
    migrate()
