from __future__ import annotations

import jwt
from fastapi import Header, HTTPException

from app.config.settings import Settings


def user_id_from_header(settings: Settings, authorization: str | None) -> str:
    if not settings.auth_required:
        return settings.dev_user_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="נדרשת התחברות")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=settings.supabase_jwt_audience,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="ההתחברות לא תקפה") from exc
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="ההתחברות לא תקפה")
    return str(subject)


def require_user(settings: Settings, authorization: str | None = Header(default=None)) -> str:
    return user_id_from_header(settings, authorization)
