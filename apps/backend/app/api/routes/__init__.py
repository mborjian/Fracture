from app.api.routes.core import router as core_router
from app.api.routes.health import router as health_router
from app.api.routes.profiles import router as profiles_router
from app.api.routes.settings import router as settings_router

__all__ = [
    "core_router",
    "health_router",
    "profiles_router",
    "settings_router",
]
