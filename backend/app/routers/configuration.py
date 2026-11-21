import json
import sqlite3
from datetime import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.database import get_connection
from backend.app.dependencies import get_current_parent
from backend.app.timeutils import utc_now


router = APIRouter(tags=["Rules and Schedules"])
Mode = Literal["normal", "study", "entertainment", "bedtime"]
Category = Literal[
    "education", "gaming", "social media", "streaming", "other"
]
Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class RuleCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    action: Literal["allow", "block"]
    category: Category = "other"
    mode: Mode | None = None
    device_id: int | None = None

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value):
        domain = value.strip().lower().rstrip(".")
        if "://" in domain or "/" in domain or " " in domain or "." not in domain:
            raise ValueError("Enter a domain such as example.com, without a URL path")
        return domain


class ModeUpdate(BaseModel):
    mode: Mode


class ActivitySettingsUpdate(BaseModel):
    blocked_attempt_threshold: int = Field(ge=2, le=100)


class ScheduleCreate(BaseModel):
    device_id: int | None = None
    mode: Mode
    start_time: time
    end_time: time
    days: list[Day] = Field(min_length=1)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_times(self):
        if self.start_time == self.end_time:
            raise ValueError("Start and end times must be different")
        if len(set(self.days)) != len(self.days):
            raise ValueError("Select each day only once")
        return self


@router.get("/rules")
def list_rules(device_id: int | None = None, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        if device_id is None:
            rows = connection.execute(
                """SELECT website_rules.*, devices.device_name
                   FROM website_rules LEFT JOIN devices ON devices.id = website_rules.device_id
                   WHERE website_rules.parent_id = ?
                   ORDER BY website_rules.domain""",
                (parent["id"],),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT website_rules.*, devices.device_name
                   FROM website_rules LEFT JOIN devices ON devices.id = website_rules.device_id
                   WHERE website_rules.parent_id = ?
                     AND (website_rules.device_id IS NULL OR website_rules.device_id = ?)
                   ORDER BY website_rules.domain""",
                (parent["id"], device_id),
            ).fetchall()
    return {"rules": [dict(row) for row in rows]}


@router.post("/rules", status_code=201)
def create_rule(rule: RuleCreate, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        if rule.device_id is not None and connection.execute(
            "SELECT 1 FROM devices WHERE id = ? AND parent_id = ?",
            (rule.device_id, parent["id"]),
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="Device not found")

        duplicate = connection.execute(
            """SELECT 1 FROM website_rules
               WHERE parent_id = ? AND domain = ? AND action = ? AND category = ?
                 AND mode IS ? AND device_id IS ?""",
            (
                parent["id"],
                rule.domain,
                rule.action,
                rule.category,
                rule.mode,
                rule.device_id,
            ),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="This website rule already exists")

        try:
            cursor = connection.execute(
                """INSERT INTO website_rules
                   (domain, action, category, mode, device_id, created_at, parent_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule.domain,
                    rule.action,
                    rule.category,
                    rule.mode,
                    rule.device_id,
                    utc_now(),
                    parent["id"],
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Could not save this website rule")
    return {"message": "Rule saved", "rule_id": cursor.lastrowid}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM website_rules WHERE id = ? AND parent_id = ?",
            (rule_id, parent["id"]),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"message": "Rule deleted"}


@router.get("/modes")
def get_modes(parent=Depends(get_current_parent)):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT setting_value FROM parent_settings
               WHERE parent_id = ? AND setting_key = 'active_mode'""",
            (parent["id"],),
        ).fetchone()
    return {
        "active_mode": row["setting_value"] if row else "normal",
        "modes": ["normal", "study", "entertainment", "bedtime"],
        "enforcement": "applied by the Hub after its next sync; active schedules take priority",
    }


@router.put("/modes/active")
def set_active_mode(payload: ModeUpdate, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO parent_settings (parent_id, setting_key, setting_value)
               VALUES (?, 'active_mode', ?)
               ON CONFLICT(parent_id, setting_key)
               DO UPDATE SET setting_value = excluded.setting_value""",
            (parent["id"], payload.mode),
        )
        connection.execute(
            """INSERT INTO activity_events
               (event_type, message, simulated, created_at, parent_id)
               VALUES ('mode_changed', ?, 1, ?, ?)""",
            (f"Active mode changed to {payload.mode.title()}", utc_now(), parent["id"]),
        )
    return {
        "active_mode": payload.mode,
        "enforcement": "applied by the Hub after its next sync",
    }


@router.get("/schedules")
def list_schedules(parent=Depends(get_current_parent)):
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT schedules.*, devices.device_name
               FROM schedules LEFT JOIN devices ON devices.id = schedules.device_id
               WHERE schedules.parent_id = ?
               ORDER BY schedules.start_time""",
            (parent["id"],),
        ).fetchall()
    schedules = []
    for row in rows:
        schedule = dict(row)
        schedule["days"] = json.loads(schedule["days"])
        schedule["enabled"] = bool(schedule["enabled"])
        schedules.append(schedule)
    return {"schedules": schedules}


@router.post("/schedules", status_code=201)
def create_schedule(schedule: ScheduleCreate, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        if schedule.device_id is not None and connection.execute(
            "SELECT 1 FROM devices WHERE id = ? AND parent_id = ?",
            (schedule.device_id, parent["id"]),
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="Device not found")
        cursor = connection.execute(
            """INSERT INTO schedules
               (device_id, mode, start_time, end_time, days, enabled, created_at, parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                schedule.device_id,
                schedule.mode,
                schedule.start_time.strftime("%H:%M"),
                schedule.end_time.strftime("%H:%M"),
                json.dumps(schedule.days),
                int(schedule.enabled),
                utc_now(),
                parent["id"],
            ),
        )
    return {"message": "Schedule saved", "schedule_id": cursor.lastrowid}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM schedules WHERE id = ? AND parent_id = ?",
            (schedule_id, parent["id"]),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"message": "Schedule deleted"}


@router.get("/settings")
def get_settings(parent=Depends(get_current_parent)):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT setting_value FROM parent_settings
               WHERE parent_id = ? AND setting_key = 'blocked_attempt_threshold'""",
            (parent["id"],),
        ).fetchone()
    return {"blocked_attempt_threshold": int(row["setting_value"]) if row else 5}


@router.put("/settings")
def update_settings(payload: ActivitySettingsUpdate, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO parent_settings (parent_id, setting_key, setting_value)
               VALUES (?, 'blocked_attempt_threshold', ?)
               ON CONFLICT(parent_id, setting_key)
               DO UPDATE SET setting_value = excluded.setting_value""",
            (parent["id"], str(payload.blocked_attempt_threshold)),
        )
    return {"blocked_attempt_threshold": payload.blocked_attempt_threshold}
