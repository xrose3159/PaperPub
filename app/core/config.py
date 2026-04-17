import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://paperpub:PaperPub2026!@127.0.0.1:3306/paperpub?charset=utf8mb4",
)

SQLITE_DB_PATH = str(BASE_DIR / "paper_pub.db")

PROJECT_NAME = "PaperPub"
API_V1_PREFIX = "/api/v1"

# ── JWT 配置 ──────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET", "paperpub_jwt_s3cret_k3y_2026!@#$")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72

# ── OpenAI / 兼容 API 配置 ──────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-gQjBbpFCYCV5Z1WfB5TdCEp6gHR0HROYc7pjKRKMXkiHNjya")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://35.220.164.252:3888/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "MiniMax-M2.5")

# ── 邮件配置 ─────────────────────────────────────────────────
# 优先使用 Resend HTTP API（走 HTTPS，不受 SMTP 端口封锁影响）
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "PaperPub <onboarding@resend.dev>")
# SMTP 作为备选（外网部署时可用）
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "PaperPub")

# ── arXiv 配置 ──────────────────────────────────────────────
ARXIV_CATEGORIES = [
    "cs.AI", "cs.CL", "cs.CV", "cs.LG", "cs.CR",
    "cs.DS", "cs.SE", "cs.RO", "cs.NE", "cs.IR",
]

# ── 研究领域选项（与 ai_category 字段一致）────────────────
INTEREST_OPTIONS = [
    ("Foundation", "🏛️ Foundation"),
    ("Generative", "🎨 Generative"),
    ("Multimodal", "👁️ Multimodal"),
    ("Reasoning", "🧠 Reasoning"),
    ("Agents", "🤖 Agents"),
    ("Core ML", "📐 Core ML"),
    ("Efficiency", "⚡ Efficiency"),
    ("Systems", "🔧 Systems"),
    ("AI Infra", "🖥️ AI Infra"),
    ("Alignment & Safety", "🛡️ Alignment & Safety"),
    ("Data & Benchmark", "📊 Data & Benchmark"),
    ("Math & Code", "🧮 Math & Code"),
    ("AI for Science", "🔬 AI for Science"),
    ("Embodied AI", "🦾 Embodied AI"),
]

# ── 评分维度（与 Score 模型字段一一对应）─────────────────────
SCORE_DIMENSIONS = [
    "novelty",        # 创新性
    "rigor",          # 数学严谨性
    "applicability",  # 应用价值
    "clarity",        # 写作清晰度
    "significance",   # 研究重要性
    "reproducibility", # 可复现性
]
