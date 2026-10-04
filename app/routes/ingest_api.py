from typing import List, Union
from fastapi import APIRouter, Header, HTTPException, status
from app.config import get_settings
from app.models.schemas import (
    AccountMetricsIngest,
    AccountMetricsItem,
    PostItem,
    PostMetricsIngest,
    PostMetricsItem,
    PostsIngest,
    SentimentIngest,
    SentimentItem,
)
from app.security import verify_api_key
from app.services.ingest_service import (
    ingest_account_metrics,
    ingest_post_metrics,
    ingest_posts,
    ingest_sentiment_metrics,
)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _check_api_key(x_api_key: str):
    settings = get_settings()
    if not verify_api_key(x_api_key, settings.INGEST_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header"
        )


@router.post("/account-metrics")
async def ingest_account_metrics_endpoint(
    payload: Union[AccountMetricsIngest, List[AccountMetricsItem]],
    x_api_key: str = Header(None, alias="X-API-Key")
):
    _check_api_key(x_api_key)
    items = payload.metrics if isinstance(payload, AccountMetricsIngest) else payload
    rows = await ingest_account_metrics(items)
    return {"status": "success", "rows_upserted": rows}


@router.post("/posts")
async def ingest_posts_endpoint(
    payload: Union[PostsIngest, List[PostItem]],
    x_api_key: str = Header(None, alias="X-API-Key")
):
    _check_api_key(x_api_key)
    items = payload.posts if isinstance(payload, PostsIngest) else payload
    rows = await ingest_posts(items)
    return {"status": "success", "rows_upserted": rows}


@router.post("/post-metrics")
async def ingest_post_metrics_endpoint(
    payload: Union[PostMetricsIngest, List[PostMetricsItem]],
    x_api_key: str = Header(None, alias="X-API-Key")
):
    _check_api_key(x_api_key)
    items = payload.metrics if isinstance(payload, PostMetricsIngest) else payload
    rows = await ingest_post_metrics(items)
    return {"status": "success", "rows_upserted": rows}


@router.post("/sentiment")
async def ingest_sentiment_endpoint(
    payload: Union[SentimentIngest, List[SentimentItem]],
    x_api_key: str = Header(None, alias="X-API-Key")
):
    _check_api_key(x_api_key)
    items = payload.sentiments if isinstance(payload, SentimentIngest) else payload
    rows = await ingest_sentiment_metrics(items)
    return {"status": "success", "rows_upserted": rows}
