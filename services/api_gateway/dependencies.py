"""FastAPI dependencies for JWT authentication and Role-Based Access Control (RBAC).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from libs.contracts.models import UserPrincipal, UserRole, utc_now
from libs.events.bus import EventEnvelope, Topics
from services.api_gateway.service_container import get_services

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme),
) -> UserPrincipal:
    """Extract and validate JWT Bearer access token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    services = get_services()
    token = credentials.credentials

    try:
        principal = services.auth_svc.verify_access_token(token)
        return principal
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError as e:
        logger.warning("Token verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token signature or format.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_roles(*allowed_roles: UserRole) -> Callable[..., UserPrincipal]:
    """Factory creating an RBAC dependency enforcing minimum allowed roles."""

    async def _role_checker(
        current_user: UserPrincipal = Depends(get_current_user),
    ) -> UserPrincipal:
        if current_user.role not in allowed_roles:
            services = get_services()
            allowed_names = [r.value for r in allowed_roles]

            # Publish security audit event
            try:
                await services.event_bus.publish(
                    EventEnvelope(
                        topic=Topics.AUDIT_EVENT,
                        payload={
                            "event_type": "UNAUTHORIZED_ACCESS_DENIED",
                            "user_id": current_user.user_id,
                            "username": current_user.username,
                            "user_role": current_user.role.value,
                            "required_roles": allowed_names,
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                )
            except Exception as e:
                logger.warning("Failed to emit audit event: %s", e)

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: role '{current_user.role.value}' is not authorized. Requires: {', '.join(allowed_names)}",
            )
        return current_user

    return _role_checker

