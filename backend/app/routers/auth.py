import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.app.database import get_connection
from backend.app.dependencies import bearer_scheme, get_current_parent
from backend.app.timeutils import utc_now


router = APIRouter(prefix="/auth", tags=["Authentication"])


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email address")
        return normalized


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 240_000
    ).hex()


@router.post("/register", status_code=201)
def register_parent(credentials: Credentials):
    salt = secrets.token_bytes(16)
    password_hash = hash_password(credentials.password, salt)
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO parents (email, password_hash, password_salt, created_at)
                   VALUES (?, ?, ?, ?)""",
                (credentials.email, password_hash, salt.hex(), utc_now()),
            )
            parent_id = cursor.lastrowid
            parent_count = connection.execute(
                "SELECT COUNT(*) AS count FROM parents"
            ).fetchone()["count"]
            if parent_count == 1:
                for table in (
                    "devices",
                    "website_rules",
                    "schedules",
                    "access_requests",
                    "temporary_permissions",
                    "activity_events",
                ):
                    connection.execute(
                        f"UPDATE {table} SET parent_id = ? WHERE parent_id IS NULL",
                        (parent_id,),
                    )
                connection.execute(
                    """INSERT OR IGNORE INTO parent_settings
                       (parent_id, setting_key, setting_value)
                       SELECT ?, setting_key, setting_value FROM app_settings""",
                    (parent_id,),
                )
                connection.execute("DELETE FROM app_settings")
            connection.executemany(
                """INSERT OR IGNORE INTO parent_settings
                   (parent_id, setting_key, setting_value)
                   VALUES (?, ?, ?)""",
                (
                    (parent_id, "active_mode", "normal"),
                    (parent_id, "blocked_attempt_threshold", "5"),
                ),
            )
    except sqlite3.IntegrityError as error:
        if "UNIQUE constraint failed: parents.email" in str(error):
            raise HTTPException(status_code=409, detail="An account already uses this email")
        raise
    return {"id": parent_id, "email": credentials.email}


@router.post("/login")
def login(credentials: Credentials):
    with get_connection() as connection:
        parent = connection.execute(
            "SELECT id, email, password_hash, password_salt FROM parents WHERE email = ?",
            (credentials.email,),
        ).fetchone()

    if parent is None or not secrets.compare_digest(
        hash_password(credentials.password, bytes.fromhex(parent["password_salt"])),
        parent["password_hash"],
    ):
        raise HTTPException(status_code=401, detail="Email or password is incorrect")

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO auth_sessions (token_hash, parent_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, parent["id"], expires_at),
        )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {"id": parent["id"], "email": parent["email"]},
    }


@router.post("/logout")
def logout(
    parent=Depends(get_current_parent),
    credentials=Depends(bearer_scheme),
):
    del parent
    token_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
    return {"message": "Signed out"}


@router.get("/me")
def current_user(parent=Depends(get_current_parent)):
    return {"user": parent}
