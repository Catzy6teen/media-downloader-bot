"""
web/auth.py – Upload endpoint authentication.

The X-Upload-Key header is compared using hmac.compare_digest to prevent
timing-based key enumeration attacks.  The raw key value is NEVER logged.
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from core.config import settings

logger = logging.getLogger(__name__)


async def verify_api_key(
    x_upload_key: Annotated[str | None, Header()] = None,
) -> None:
    """
    FastAPI dependency that enforces X-Upload-Key authentication.

    Raises HTTP 401 for missing or incorrect keys.
    The real key value is never included in any log message or HTTP response.
    """
    if x_upload_key is None:
        logger.warning("Upload attempt with missing X-Upload-Key header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Upload-Key header",
        )

    # Constant-time comparison – prevents timing oracle attacks
    if not hmac.compare_digest(
        x_upload_key.encode("utf-8"),
        settings.UPLOAD_KEY.encode("utf-8"),
    ):
        logger.warning("Upload attempt with invalid X-Upload-Key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Upload-Key",
        )


# Convenience alias for use in route signatures
RequireKey = Annotated[None, Depends(verify_api_key)]
