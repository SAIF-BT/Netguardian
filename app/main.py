
from fastapi import FastAPI
from app.routers import devices

app = FastAPI(
    title="NetGuardian API",
    description="Backend for the NetGuardian parental control system",
    version="1.0.0"
)

app.include_router(devices.router)

@app.get("/")
def home():
    return {
        "message": "Welcome to NetGuardian!",
        "status": "Backend is running"
    }
