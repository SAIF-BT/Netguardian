import json

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from datetime import datetime, timezone
import os
from backend.app.database import get_connection

router = APIRouter(prefix="/hub", tags=["Hub"])

HUB_API_KEY = os.environ.get("NETGUARDIAN_HUB_API_KEY", "dev-hub-key-123")

def get_now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

async def verify_hub_key(x_api_key: str = Header(...)):
    if x_api_key != HUB_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return True

class Heartbeat(BaseModel):
    hub_id: str

class LogEntry(BaseModel):
    device_id: int = None
    domain: str
    category: str = "Uncategorized"
    decision: str
    timestamp: str = None

class LogBatch(BaseModel):
    logs: list[LogEntry]

class AlertPayload(BaseModel):
    device_id: int = None
    domain: str
    count: int
    message: str

@router.post("/heartbeat", dependencies=[Depends(verify_hub_key)])
async def heartbeat(data: Heartbeat):
    now = get_now_iso()
    with get_connection() as conn:
        conn.execute(
            "UPDATE service_status SET is_online = 1, updated_at = ? WHERE service = 'hub'",
            (now,),
        )
    return {"status": "ok", "timestamp": now}

@router.get("/config", dependencies=[Depends(verify_hub_key)])
async def get_hub_config():
    with get_connection() as conn:
        devices = [
            dict(row)
            for row in conn.execute(
                """SELECT devices.*,
                          COALESCE(
                              (SELECT setting_value FROM parent_settings
                               WHERE parent_settings.parent_id = devices.parent_id
                                 AND setting_key = 'active_mode'),
                              'normal'
                          ) AS active_mode
                   FROM devices"""
            ).fetchall()
        ]
        rules = [dict(row) for row in conn.execute("SELECT * FROM website_rules").fetchall()]
        schedules = []
        for row in conn.execute("SELECT * FROM schedules").fetchall():
            schedule = dict(row)
            schedule["days"] = json.loads(schedule["days"])
            schedule["enabled"] = bool(schedule["enabled"])
            schedules.append(schedule)
        perms = [dict(row) for row in conn.execute("SELECT * FROM temporary_permissions").fetchall()]

    return {
        "devices": devices,
        "rules": rules,
        "schedules": schedules,
        "temporary_permissions": perms,
        "active_mode": "normal",
    }

@router.post("/logs", dependencies=[Depends(verify_hub_key)])
async def post_logs(batch: LogBatch):
    now = get_now_iso()
    with get_connection() as conn:
        for log in batch.logs:
            ts = log.timestamp or now
            device = (
                conn.execute(
                    "SELECT parent_id FROM devices WHERE id = ?", (log.device_id,)
                ).fetchone()
                if log.device_id is not None
                else None
            )
            conn.execute(
                """INSERT INTO activity_events
                   (event_type, device_id, domain, category, decision, message, created_at, parent_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "dns_lookup",
                    log.device_id,
                    log.domain,
                    log.category,
                    log.decision,
                    f"DNS query for {log.domain} ({log.category}) - {log.decision.upper()}",
                    ts,
                    device["parent_id"] if device else None,
                )
            )
    return {"status": "recorded", "count": len(batch.logs)}

@router.post("/alerts", dependencies=[Depends(verify_hub_key)])
async def post_alert(alert: AlertPayload):
    now = get_now_iso()
    with get_connection() as conn:
        device = (
            conn.execute(
                "SELECT parent_id FROM devices WHERE id = ?", (alert.device_id,)
            ).fetchone()
            if alert.device_id is not None
            else None
        )
        conn.execute(
            """INSERT INTO activity_events
               (event_type, device_id, domain, category, decision, message, created_at, parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "alert",
                alert.device_id,
                alert.domain,
                "Security",
                "blocked",
                alert.message,
                now,
                device["parent_id"] if device else None,
            )
        )
    return {"status": "alert_recorded"}
