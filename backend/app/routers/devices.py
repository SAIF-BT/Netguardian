import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.database import get_connection
from backend.app.dependencies import get_current_parent


router = APIRouter(prefix="/devices", tags=["Devices"])


class DeviceCreate(BaseModel):
    device_name: str = Field(min_length=1, max_length=80)
    device_type: str = Field(default="Other", min_length=1, max_length=40)
    device_identifier: str | None = Field(default=None, max_length=120)


class DeviceUpdate(BaseModel):
    device_name: str = Field(min_length=1, max_length=80)
    device_type: str = Field(min_length=1, max_length=40)


@router.get("/")
def get_devices(parent=Depends(get_current_parent)):
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT id, device_name, device_type, device_identifier, is_active, created_at
               FROM devices WHERE parent_id = ? ORDER BY id""",
            (parent["id"],),
        ).fetchall()
    return {"devices": [dict(row) for row in rows]}


@router.post("/", status_code=201)
def register_device(device: DeviceCreate, parent=Depends(get_current_parent)):
    identifier = device.device_identifier or f"device-{uuid.uuid4().hex[:12]}"
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO devices
                   (device_name, device_type, device_identifier, parent_id)
                   VALUES (?, ?, ?, ?)""",
                (
                    device.device_name.strip(),
                    device.device_type.strip(),
                    identifier.strip(),
                    parent["id"],
                ),
            )
            device_id = cursor.lastrowid
    except sqlite3.IntegrityError as error:
        if "device_identifier" in str(error):
            raise HTTPException(
                status_code=409, detail="A device already uses this identifier"
            )
        raise
    return {"message": "Device registered", "device_id": device_id}


@router.get("/{device_id}")
def get_device(device_id: int, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT id, device_name, device_type, device_identifier, is_active, created_at
               FROM devices WHERE id = ? AND parent_id = ?""",
            (device_id, parent["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"device": dict(row)}


@router.put("/{device_id}/status")
def update_device_status(
    device_id: int, is_active: bool, parent=Depends(get_current_parent)
):
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE devices SET is_active = ? WHERE id = ? AND parent_id = ?",
            (int(is_active), device_id, parent["id"]),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"message": "Device status updated", "device_id": device_id, "is_active": is_active}


@router.put("/{device_id}")
def update_device(
    device_id: int, device: DeviceUpdate, parent=Depends(get_current_parent)
):
    with get_connection() as connection:
        cursor = connection.execute(
            """UPDATE devices SET device_name = ?, device_type = ?
               WHERE id = ? AND parent_id = ?""",
            (device.device_name.strip(), device.device_type.strip(), device_id, parent["id"]),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"message": "Device updated", "device_id": device_id}


@router.delete("/{device_id}")
def delete_device(device_id: int, parent=Depends(get_current_parent)):
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM devices WHERE id = ? AND parent_id = ?",
            (device_id, parent["id"]),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"message": "Device deleted", "device_id": device_id}
