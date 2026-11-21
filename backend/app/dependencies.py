import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.database import get_connection


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_parent(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Sign in to continue")

    token_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        row = connection.execute(
            """SELECT parents.id, parents.email
               FROM auth_sessions
               JOIN parents ON parents.id = auth_sessions.parent_id
               WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?""",
            (token_hash, datetime.now(timezone.utc).isoformat()),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=401, detail="Session expired; sign in again")
    return dict(row)
