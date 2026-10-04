from fastapi import APIRouter, Header, HTTPException, status
from app.config import get_settings
from app.models.schemas import CredentialsResponse
from app.security import verify_api_key
from app.services.token_service import TokenServiceError, get_valid_credentials

router = APIRouter(prefix="/api/internal", tags=["internal"])


@router.get("/credentials/{platform}", response_model=CredentialsResponse)
async def get_credentials(
    platform: str,
    x_api_key: str = Header(None, alias="X-API-Key")
):
    settings = get_settings()
    if not verify_api_key(x_api_key, settings.INGEST_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header"
        )

    try:
        creds = await get_valid_credentials(platform.lower())
        return CredentialsResponse(
            access_token=creds["access_token"],
            account_id=creds.get("account_id"),
            account_handle=creds.get("account_handle"),
            expires_at=creds.get("expires_at"),
        )
    except TokenServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
