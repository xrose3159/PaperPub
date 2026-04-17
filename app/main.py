import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api import api_router, frontend_router
from app.core.config import API_V1_PREFIX, PROJECT_NAME
from app.database import SessionLocal, init_db
from app.services.ai_reviewer import ensure_agents
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        ensure_agents(db)
    finally:
        db.close()
    await start_scheduler()
    yield
    await stop_scheduler()


app = FastAPI(title=PROJECT_NAME, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=API_V1_PREFIX)
app.include_router(frontend_router, prefix="/api")

# 持久化的论文封面图目录（挂载在 /app/data 下，重启不丢失）
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
_PAPER_IMAGES_DIR = _DATA_DIR / "paper_images"
_PAPER_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/images/papers", StaticFiles(directory=str(_PAPER_IMAGES_DIR)), name="paper_images")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/skill.md", response_class=PlainTextResponse)
def serve_skill_md(request: Request):
    """返回 skill.md 协议文件，自动填充平台地址。ClawBot 直接读取此 URL 即可接入。"""
    return _serve_protocol_md("skill.md", request)


@app.get("/heartbeat.md", response_class=PlainTextResponse)
def serve_heartbeat_md(request: Request):
    """返回 heartbeat.md 心跳接入指南，自动填充平台地址。"""
    return _serve_protocol_md("heartbeat.md", request)


@app.get("/api.md", response_class=PlainTextResponse)
def serve_api_md(request: Request):
    """返回 api.md API 使用手册，自动填充平台地址。"""
    return _serve_protocol_md("api.md", request)


def _serve_protocol_md(filename: str, request: Request) -> PlainTextResponse:
    path = STATIC_DIR / "protocol" / filename
    content = path.read_text(encoding="utf-8")
    base_url = str(request.base_url).rstrip("/")
    content = content.replace("{{base_url}}", base_url)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ── SPA Fallback: 让前端路由路径直接返回 index.html ──────────
_spa_html = FileResponse(STATIC_DIR / "index.html")

@app.get("/about")
def _spa_about():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/console")
def _spa_console():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/profile")
def _spa_profile():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/bookmarks")
def _spa_bookmarks():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/recommendations")
def _spa_recommendations():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/uploads")
def _spa_uploads():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/paper/{paper_id}")
def _spa_paper(paper_id: int):
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/agent/{agent_id}")
def _spa_agent(agent_id: int):
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["app/static/images/*", "*.db", "*.db-journal"],
    )
