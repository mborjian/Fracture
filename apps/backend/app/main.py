from __future__ import annotations

from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import core_router, health_router, profiles_router, settings_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.database import init_db
from app.services.ping import ProfilePingService
from app.services.runtime import CoreRuntimeService
from app.ws.hub import WsEventHub


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()

    hub = WsEventHub()
    runtime = CoreRuntimeService(publish_event=hub.publish)
    ping_service = ProfilePingService(publish_event=hub.publish, runtime_service=runtime)
    app.state.ws_hub = hub
    app.state.runtime_service = runtime
    app.state.ping_service = ping_service

    from app.services.profiles import get_active_profile

    await runtime.set_active_profile(await get_active_profile())

    yield

    await ping_service.shutdown()
    await runtime.shutdown()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(core_router)
app.include_router(profiles_router)
app.include_router(settings_router)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    hub: WsEventHub = app.state.ws_hub
    runtime: CoreRuntimeService = app.state.runtime_service
    await hub.connect(websocket)

    try:
        await hub.publish("status", await runtime.get_status())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.disconnect(websocket)
        with suppress(RuntimeError):
            await websocket.close()


@app.get("/api/meta")
async def app_meta() -> dict:
    return {
        "appName": settings.app_name,
        "version": settings.version,
        "host": settings.host,
        "port": settings.port,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
