from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from backend.app.database import initialize_database
from backend.app.dependencies import get_current_parent
from backend.app.routers import auth, configuration, devices, monitoring, hardware


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIRECTORY = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="NetGuardian API",
    description="Parent-control prototype API. Hub enforcement and ESP32 events are simulated.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router, prefix="/api")
app.include_router(
    devices.router, prefix="/api", dependencies=[Depends(get_current_parent)]
)
app.include_router(
    devices.router,
    dependencies=[Depends(get_current_parent)],
    include_in_schema=False,
)
app.include_router(
    configuration.router, prefix="/api", dependencies=[Depends(get_current_parent)]
)
app.include_router(
    monitoring.router, prefix="/api", dependencies=[Depends(get_current_parent)]
)
app.include_router(hardware.router, prefix="/api")


@app.get("/api/health", tags=["Health"])
def health():
    return {
        "status": "ok",
        "service": "NetGuardian API",
        "hardware_integration": "simulated",
    }


app.mount("/", StaticFiles(directory=FRONTEND_DIRECTORY, html=True), name="frontend")
