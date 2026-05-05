from fastapi import FastAPI

from ml_service.routers.admin import router as admin_router
from ml_service.routers.auth import router as auth_router
from ml_service.routers.balance import router as balance_router
from ml_service.routers.catalog import router as catalog_router
from ml_service.routers.history import router as history_router
from ml_service.routers.predictions import router as predictions_router
from ml_service.routers.system import router as system_router
from ml_service.routers.users import router as users_router


def register_routers(app: FastAPI) -> None:
    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(balance_router)
    app.include_router(catalog_router)
    app.include_router(predictions_router)
    app.include_router(history_router)
    app.include_router(admin_router)
