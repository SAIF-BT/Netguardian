from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.app.database import get_connection
from backend.app.dependencies import get_current_parent
from backend.app.timeutils import utc_now


router = APIRouter(tags=["Monitoring"])
child_router = APIRouter(tags=["Child access requests"])


class AccessRequestCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    reason: str = Field(default="", max_length=500)
    device_id: int | None = None


class ChildAccessRequestCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    reason: str = Field(default="", max_length=500)


class AccessDecision(BaseModel):
    decision: Literal["temporary", "deny"]
    duration_minutes: int = Field(default=30, ge=1, le=1440)


class ActivityCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    category: str = Field(default="other", max_length=40)
    decision: Literal["allowed", "blocked"]
    device_id: int | None = None


class ServiceStatusUpdate(BaseModel):
    hub_online: bool
    esp32_online: bool


class TamperUpdate(BaseModel):
    opened: bool


def validate_domain(value):
    domain = value.strip().lower().rstrip(".")
    if "://" in domain or "/" in domain or " " in domain or "." not in domain:
        raise HTTPException(status_code=422, detail="Enter a domain such as example.com")
    return domain


@child_router.post("/child/access-requests", status_code=201)
def create_child_access_request(
    payload: ChildAccessRequestCreate, request: Request
):
    domain = validate_domain(payload.domain)
    client_ip = request.client.host if request.client else ""
    with get_connection() as connection:
        device = connection.execute(
            """SELECT id, parent_id FROM devices
               WHERE device_identifier = ? AND is_active = 1""",
            (client_ip,),
        ).fetchone()
        if device is None:
            raise HTTPException(
                status_code=403,
                detail="This device is not registered and active in NetGuardian.",
            )

        created_at = utc_now()
        cursor = connection.execute(
            """INSERT INTO access_requests
               (device_id, domain, reason, status, created_at, parent_id)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (
                device["id"],
                domain,
                payload.reason.strip(),
                created_at,
                device["parent_id"],
            ),
        )
        connection.execute(
            """INSERT INTO activity_events
               (event_type, device_id, domain, message, created_at, parent_id)
               VALUES ('access_request', ?, ?, ?, ?, ?)""",
            (
                device["id"],
                domain,
                f"Access requested for {domain}",
                created_at,
                device["parent_id"],
            ),
        )
    return {
        "message": "Access request sent to the parent.",
        "request_id": cursor.lastrowid,
    }


@router.get("/access-requests")
def list_access_requests(parent=Depends(get_current_parent)):
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT access_requests.*, devices.device_name,
                      temporary_permissions.expires_at AS permission_expires_at
               FROM access_requests
               LEFT JOIN devices ON devices.id = access_requests.device_id
               LEFT JOIN temporary_permissions
                 ON temporary_permissions.request_id = access_requests.id
               WHERE access_requests.parent_id = ?
               ORDER BY access_requests.created_at DESC""",
            (parent["id"],),
        ).fetchall()
    return {"requests": [dict(row) for row in rows]}


@router.get("/permissions")
def list_temporary_permissions(parent=Depends(get_current_parent)):
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT temporary_permissions.*, devices.device_name
               FROM temporary_permissions
               LEFT JOIN devices ON devices.id = temporary_permissions.device_id
               WHERE temporary_permissions.parent_id = ?
               ORDER BY temporary_permissions.expires_at DESC""",
            (parent["id"],),
        ).fetchall()
    return {
        "permissions": [
            {**dict(row), "active": row["expires_at"] > now} for row in rows
        ],
        "enforcement": "applied by the Hub after its next sync",
    }


@router.post("/access-requests", status_code=201)
def create_access_request(
    request: AccessRequestCreate, parent=Depends(get_current_parent)
):
    domain = validate_domain(request.domain)
    with get_connection() as connection:
        if request.device_id is not None and connection.execute(
            "SELECT 1 FROM devices WHERE id = ? AND parent_id = ?",
            (request.device_id, parent["id"]),
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="Device not found")
        cursor = connection.execute(
            """INSERT INTO access_requests
               (device_id, domain, reason, status, created_at, parent_id)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (request.device_id, domain, request.reason.strip(), utc_now(), parent["id"]),
        )
        connection.execute(
            """INSERT INTO activity_events
               (event_type, device_id, domain, message, simulated, created_at, parent_id)
               VALUES ('access_request', ?, ?, ?, 1, ?, ?)""",
            (
                request.device_id,
                domain,
                f"Access requested for {domain}",
                utc_now(),
                parent["id"],
            ),
        )
    return {"message": "Access request sent to the parent app", "request_id": cursor.lastrowid}


@router.post("/access-requests/{request_id}/decision")
def decide_access_request(
    request_id: int,
    payload: AccessDecision,
    parent=Depends(get_current_parent),
):
    with get_connection() as connection:
        request = connection.execute(
            "SELECT * FROM access_requests WHERE id = ? AND parent_id = ?",
            (request_id, parent["id"]),
        ).fetchone()
        if request is None:
            raise HTTPException(status_code=404, detail="Access request not found")
        if request["status"] != "pending":
            raise HTTPException(status_code=409, detail="This request has already been decided")

        decided_at = utc_now()
        status = "temporary" if payload.decision == "temporary" else "denied"
        connection.execute(
            "UPDATE access_requests SET status = ?, decided_at = ? WHERE id = ?",
            (status, decided_at, request_id),
        )
        if payload.decision == "temporary":
            expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=payload.duration_minutes)
            ).isoformat()
            connection.execute(
                """INSERT INTO temporary_permissions
                   (request_id, device_id, domain, expires_at, parent_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    request_id,
                    request["device_id"],
                    request["domain"],
                    expires_at,
                    parent["id"],
                ),
            )
        connection.execute(
            """INSERT INTO activity_events
               (event_type, device_id, domain, message, simulated, created_at, parent_id)
               VALUES ('access_decision', ?, ?, ?, 0, ?, ?)""",
            (
                request["device_id"],
                request["domain"],
                f"Request for {request['domain']} {status}",
                decided_at,
                parent["id"],
            ),
        )
    return {"message": f"Request {status}", "status": status}


@router.get("/activity")
def get_activity(
    limit: int = Query(default=50, ge=1, le=200),
    parent=Depends(get_current_parent),
):
    with get_connection() as connection:
        totals = connection.execute(
            """SELECT
                 SUM(CASE WHEN decision IN ('allowed', 'allow') THEN 1 ELSE 0 END) AS allowed,
                 SUM(CASE WHEN decision IN ('blocked', 'block') THEN 1 ELSE 0 END) AS blocked
               FROM activity_events WHERE parent_id = ?""",
            (parent["id"],),
        ).fetchone()
        categories = connection.execute(
            """SELECT category, COUNT(*) AS count
               FROM activity_events WHERE event_type = 'network' AND parent_id = ?
               GROUP BY category ORDER BY category""",
            (parent["id"],),
        ).fetchall()
        rows = connection.execute(
            """SELECT activity_events.*, devices.device_name
               FROM activity_events
               LEFT JOIN devices ON devices.id = activity_events.device_id
               WHERE activity_events.parent_id = ?
               ORDER BY activity_events.id DESC LIMIT ?""",
            (parent["id"], limit),
        ).fetchall()
    return {
        "totals": {"allowed": totals["allowed"] or 0, "blocked": totals["blocked"] or 0},
        "categories": [dict(row) for row in categories],
        "events": [dict(row) for row in rows],
        "simulated": True,
    }


@router.post("/simulator/activity", status_code=201)
def simulate_activity(event: ActivityCreate, parent=Depends(get_current_parent)):
    domain = validate_domain(event.domain)
    with get_connection() as connection:
        if event.device_id is not None and connection.execute(
            "SELECT 1 FROM devices WHERE id = ? AND parent_id = ?",
            (event.device_id, parent["id"]),
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="Device not found")

        created_at = utc_now()
        connection.execute(
            """INSERT INTO activity_events
               (event_type, device_id, domain, category, decision, message, simulated, created_at, parent_id)
               VALUES ('network', ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                event.device_id,
                domain,
                event.category.strip().lower(),
                event.decision,
                f"{domain} {event.decision}",
                created_at,
                parent["id"],
            ),
        )

        if event.decision == "blocked":
            threshold_row = connection.execute(
                """SELECT setting_value FROM parent_settings
                   WHERE parent_id = ? AND setting_key = 'blocked_attempt_threshold'""",
                (parent["id"],),
            ).fetchone()
            threshold = int(threshold_row["setting_value"]) if threshold_row else 5
            start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            count = connection.execute(
                """SELECT COUNT(*) AS count FROM activity_events
                   WHERE event_type = 'network' AND decision = 'blocked' AND parent_id = ?
                     AND domain = ? AND device_id IS ? AND created_at >= ?""",
                (parent["id"], domain, event.device_id, start),
            ).fetchone()["count"]
            if count == threshold:
                connection.execute(
                    """INSERT INTO activity_events
                       (event_type, device_id, domain, message, simulated, created_at, parent_id)
                       VALUES ('repeated_attempt', ?, ?, ?, 1, ?, ?)""",
                    (
                        event.device_id,
                        domain,
                        f"Repeated blocked attempts for {domain} reached {threshold}",
                        created_at,
                        parent["id"],
                    ),
                )
    return {"message": "Simulated activity recorded", "simulated": True}


@router.get("/status")
def get_status():
    now = datetime.now(timezone.utc)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT service, is_online, status_message, updated_at FROM service_status"
        ).fetchall()

    services = {}
    for row in rows:
        updated_at = row["updated_at"]
        online = bool(row["is_online"])

        if updated_at != "1970-01-01T00:00:00+00:00":
            try:
                last_update = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                if last_update.tzinfo is None:
                    last_update = last_update.replace(tzinfo=timezone.utc)

                if (now - last_update).total_seconds() > 45:
                    online = False
            except ValueError:
                pass

        services[row["service"]] = {
            "online": online,
            "message": row["status_message"] if online else "Heartbeat expired or not connected",
            "updated_at": updated_at,
        }
    return {"hub": services.get("hub"), "esp32": services.get("esp32"), "simulated": False}


@router.post("/simulator/status")
def simulate_status(payload: ServiceStatusUpdate, parent=Depends(get_current_parent)):
    updated_at = utc_now()
    values = (
        ("hub", payload.hub_online, "Connected" if payload.hub_online else "Offline"),
        ("esp32", payload.esp32_online, "Connected" if payload.esp32_online else "Offline"),
    )
    with get_connection() as connection:
        connection.executemany(
            """INSERT INTO service_status (service, is_online, status_message, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(service) DO UPDATE SET
                 is_online = excluded.is_online,
                 status_message = excluded.status_message,
                 updated_at = excluded.updated_at""",
            ((service, int(online), message, updated_at) for service, online, message in values),
        )
        connection.execute(
            """INSERT INTO activity_events
               (event_type, message, simulated, created_at, parent_id)
               VALUES ('status', 'Hub and ESP32 simulator status updated', 1, ?, ?)""",
            (updated_at, parent["id"]),
        )
    return {"message": "Simulated service status updated", "simulated": True}


@router.post("/simulator/tamper", status_code=201)
def simulate_tamper(payload: TamperUpdate, parent=Depends(get_current_parent)):
    updated_at = utc_now()
    state = "Tamper detected" if payload.opened else "Enclosure secure"
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO service_status (service, is_online, status_message, updated_at)
               VALUES ('esp32', 1, ?, ?)
               ON CONFLICT(service) DO UPDATE SET
                 is_online = 1, status_message = excluded.status_message,
                 updated_at = excluded.updated_at""",
            (state, updated_at),
        )
        connection.execute(
            """INSERT INTO activity_events
               (event_type, message, simulated, created_at, parent_id)
               VALUES ('tamper', ?, 1, ?, ?)""",
            (state, updated_at, parent["id"]),
        )
    return {"message": state, "simulated": True}
