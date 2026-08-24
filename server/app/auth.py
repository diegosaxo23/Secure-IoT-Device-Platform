from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import get_settings


basic_security = HTTPBasic(auto_error=False)


def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(basic_security)) -> str:
    """Require admin authentication via HTTP Basic Auth OR active session."""
    # Try HTTP Basic Auth first
    if credentials is not None:
        settings = get_settings()
        username_ok = secrets.compare_digest(
            credentials.username.encode("utf-8"),
            settings.dashboard_username.encode("utf-8"),
        )
        password_ok = secrets.compare_digest(
            credentials.password.encode("utf-8"),
            settings.dashboard_password.encode("utf-8"),
        )
        if username_ok and password_ok:
            # Mark session as authenticated for future requests
            request.session["admin_authenticated"] = True
            request.session["admin_user"] = credentials.username
            return credentials.username
    
    # Fall back to session-based authentication
    if request.session.get("admin_authenticated"):
        return request.session.get("admin_user", "admin")
    
    # Neither auth method worked
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Basic"},
    )
