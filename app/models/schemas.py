from typing import List, Optional
from pydantic import BaseModel, Field


class AccountMetricsItem(BaseModel):
    platform_id: str
    date: str
    followers: int = 0
    views: int = 0
    reach: int = 0
    impressions: int = 0
    watch_time_sec: float = 0.0
    engagement_count: int = 0


class AccountMetricsIngest(BaseModel):
    metrics: List[AccountMetricsItem]


class PostItem(BaseModel):
    id: Optional[str] = None
    platform_id: str
    external_id: str
    title: str = ""
    url: str = ""
    thumbnail_url: str = ""
    post_type: str = "video"
    published_at: Optional[str] = None
    streamed_at: Optional[str] = None
    privacy_status: Optional[str] = "public"


class PostsIngest(BaseModel):
    posts: List[PostItem]


class PostMetricsItem(BaseModel):
    post_id: Optional[str] = None
    external_id: Optional[str] = None
    date: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    avg_watch_sec: float = 0.0


class PostMetricsIngest(BaseModel):
    metrics: List[PostMetricsItem]


class KpiTargetCreate(BaseModel):
    platform_id: Optional[str] = None
    metric: str
    period: str  # 'weekly' or 'monthly'
    target_value: float
    start_date: str
    end_date: str


class CredentialsResponse(BaseModel):
    access_token: str
    account_id: Optional[str] = None
    account_handle: Optional[str] = None
    expires_at: Optional[int] = None


class SentimentItem(BaseModel):
    platform_id: str
    date: str
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    tone_enthusiastic: int = 0
    tone_informative: int = 0
    tone_curious: int = 0
    tone_critical: int = 0
    dominant_tone: str = "Antusias & Apresiatif"
    sample_positive_quote: Optional[str] = None
    sample_negative_quote: Optional[str] = None


class SentimentIngest(BaseModel):
    sentiments: List[SentimentItem]
