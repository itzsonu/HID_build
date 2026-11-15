import secrets

from fastapi import HTTPException, Request

from app.core.config import settings


def require_admin(request: Request) -> None:
    configured_secret = settings.ADMIN_SECRET
    if configured_secret is None or not configured_secret.get_secret_value():
        raise HTTPException(
            status_code=503,
            detail="Admin operations are disabled; configure ADMIN_SECRET.",
        )

    scheme, separator, supplied_secret = request.headers.get(
        "authorization", ""
    ).partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not supplied_secret
        or not secrets.compare_digest(
            supplied_secret,
            configured_secret.get_secret_value(),
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Admin authorization is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
