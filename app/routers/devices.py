
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.database import get_connection

router = APIRouter(
    prefix="/devices",
    tags=["Devices"]
)

class DeviceCreate(BaseModel):
    device_name: str
    device_type: str
    device_identifier: str

@router.get("/")
def get_devices():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT * FROM devices")
    devices = cursor.fetchall()

    connection.close()

    return {"devices": devices}

@router.post("/")
def register_device(device: DeviceCreate):
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """INSERT INTO devices
            (device_name, device_type, device_identifier)
            VALUES (?, ?, ?)""",
            (device.device_name, device.device_type, device.device_identifier)
        )

        connection.commit()

        return {
            "message": "Device registered successfully",
            "device_id": cursor.lastrowid
        }

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Device identifier already exists or registration failed"
        )

    
    finally:
        connection.close()


@router.put("/{device_id}/status")
def update_device_status(device_id: int, is_active: bool):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE devices SET is_active = ? WHERE id = ?",
        (int(is_active), device_id)
    )

    connection.commit()

    if cursor.rowcount == 0:
        connection.close()
        raise HTTPException(status_code=404, detail="Device not found")

    connection.close()

    return {
        "message": "Device status updated successfully",
        "device_id": device_id,
        "is_active": is_active
    }
@router.get("/{device_id}")
def get_device(device_id: int):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT * FROM devices WHERE id = ?",
        (device_id,)
    )

    device = cursor.fetchone()
    connection.close()

    if device is None:
        raise HTTPException(
            status_code=404,
            detail="Device not found"
        )

    return {"device": device}

class DeviceUpdate(BaseModel):
    device_name: str
    device_type: str


@router.put("/{device_id}")
def update_device(device_id: int, device: DeviceUpdate):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """UPDATE devices
        SET device_name = ?, device_type = ?
        WHERE id = ?""",
        (device.device_name, device.device_type, device_id)
    )

    connection.commit()

    if cursor.rowcount == 0:
        connection.close()
        raise HTTPException(
            status_code=404,
            detail="Device not found"
        )

    connection.close()

    return {
        "message": "Device updated successfully",
        "device_id": device_id
    }


@router.delete("/{device_id}")
def delete_device(device_id: int):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "DELETE FROM devices WHERE id = ?",
        (device_id,)
    )

    connection.commit()

    if cursor.rowcount == 0:
        connection.close()
        raise HTTPException(
            status_code=404,
            detail="Device not found"
        )

    connection.close()

    return {
        "message": "Device deleted successfully",
        "device_id": device_id
    }
