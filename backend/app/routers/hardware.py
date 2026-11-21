from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from datetime import datetime, timezone
import os
from backend.app.database import get_connection

router = APIRouter(prefix="/hardware", tags=["Hardware"])

# In a real app, use a proper secrets manager or environment variable
HARDWARE_API_KEY = os.environ.get("NETGUARDIAN_HARDWARE_API_KEY", "dev-secret-key-123")

def get_now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

async def verify_hardware_key(x_api_key: str = Header(...)):
    if x_api_key != HARDWARE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return True

class Heartbeat(BaseModel):
    esp32_id: str

class Tamper(BaseModel):
    esp32_id: str
    message: str = "Enclosure opened"

@router.post("/heartbeat", dependencies=[Depends(verify_hardware_key)])
async def heartbeat(data: Heartbeat):
    now = get_now_iso()
    with get_connection() as conn:
        conn.execute(
            "UPDATE service_status SET is_online = 1, updated_at = ? WHERE service = 'esp32'",
            (now,),
        )
    return {"status": "ok", "timestamp": now}

@router.post("/tamper", dependencies=[Depends(verify_hardware_key)])
async def tamper(data: Tamper):
    now = get_now_iso()
    message = f"Tamper event: {data.message}"
    with get_connection() as conn:
        conn.execute(
            "UPDATE service_status SET status_message = ?, updated_at = ? WHERE service = 'esp32'",
            ("Tamper detected", now),
        )
        conn.execute(
            """INSERT INTO activity_events
               (event_type, message, created_at)
               VALUES (?, ?, ?)""",
            ("tamper", message, now),
        )
    return {"status": "recorded"}

@router.post("/tamper-cleared", dependencies=[Depends(verify_hardware_key)])
async def tamper_cleared():
    now = get_now_iso()
    with get_connection() as conn:
        conn.execute(
            "UPDATE service_status SET status_message = ?, updated_at = ? WHERE service = 'esp32'",
            ("Enclosure secure", now),
        )
        conn.execute(
            """INSERT INTO activity_events
               (event_type, message, created_at)
               VALUES (?, ?, ?)""",
            ("tamper_cleared", "Tamper alarm reset", now),
        )
    return {"status": "recorded"}
