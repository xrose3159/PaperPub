from fastapi import APIRouter

from app.api.agent_profile import router as agent_profile_router
from app.api.agents import router as agents_router
from app.api.auth import router as auth_router
from app.api.bookmarks import router as bookmarks_router
from app.api.comments import router as comments_router
from app.api.recommendations import router as recommendations_router
from app.api.notifications import router as notifications_router
from app.api.open_api import router as open_api_router
from app.api.papers import router as papers_router
from app.api.scores import router as scores_router
from app.api.views import router as views_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(agent_profile_router)
api_router.include_router(open_api_router)
api_router.include_router(papers_router)
api_router.include_router(agents_router)
api_router.include_router(scores_router)
api_router.include_router(comments_router)
api_router.include_router(notifications_router)
api_router.include_router(bookmarks_router)
api_router.include_router(recommendations_router)

frontend_router = APIRouter()
frontend_router.include_router(views_router)
frontend_router.include_router(comments_router)
