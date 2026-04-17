"""数据库迁移：创建 users 表，给 agents 表添加 owner_id / is_claimed 字段。"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import DATABASE_URL

db_path = DATABASE_URL.replace("sqlite:///", "")


def migrate():
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(64) NOT NULL UNIQUE,
            email VARCHAR(256) NOT NULL UNIQUE,
            password_hash VARCHAR(256) NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("PRAGMA table_info(agents)")
    cols = {r[1] for r in cur.fetchall()}

    if "owner_id" not in cols:
        cur.execute("ALTER TABLE agents ADD COLUMN owner_id INTEGER REFERENCES users(id)")
        print("新增字段: agents.owner_id")

    if "is_claimed" not in cols:
        cur.execute("ALTER TABLE agents ADD COLUMN is_claimed BOOLEAN DEFAULT 0")
        print("新增字段: agents.is_claimed")

    conn.commit()
    conn.close()
    print("迁移完成。")


if __name__ == "__main__":
    migrate()
