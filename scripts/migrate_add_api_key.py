"""数据库迁移：给 agents 表添加 api_key 字段。"""
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import DATABASE_URL

db_path = DATABASE_URL.replace("sqlite:///", "")

def migrate():
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(agents)")
    cols = {r[1] for r in cur.fetchall()}
    if "api_key" not in cols:
        cur.execute("ALTER TABLE agents ADD COLUMN api_key VARCHAR(64)")
        conn.commit()
        print("迁移完成，新增字段: api_key")
    else:
        print("api_key 字段已存在，无需迁移")
    conn.close()

if __name__ == "__main__":
    migrate()
