import pytest
from app.db import execute
from app.services.kpi_service import (
    format_number_id,
    format_date_dmy,
    get_kpi_summary,
    get_kpi_targets_progress,
)


def test_indonesian_number_formatting():
    assert format_number_id(500) == "500"
    assert format_number_id(3400) == "3,4 rb"
    assert format_number_id(1200000) == "1,2 jt"
    assert format_number_id(1300000) == "1,3 jt"
    assert format_number_id(2500000000) == "2,5 M"
    assert format_number_id(0) == "0"
    assert format_number_id(None) == "0"


def test_format_date_dmy():
    assert format_date_dmy("2026-09-08") == "08/09/2026"
    assert format_date_dmy("2026-12-31T23:59:59Z") == "31/12/2026"
    assert format_date_dmy(None) == "-"
    assert format_date_dmy("") == "-"


@pytest.mark.asyncio
async def test_kpi_summary_and_growth_calculation():
    # Insert metrics over 2 dates
    await execute("DELETE FROM account_daily_metrics")
    
    # Day 1: 10,000 followers, 2,000 views, 100 engagement
    await execute(
        """
        INSERT INTO account_daily_metrics (
            platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
        ) VALUES ('youtube', '2026-10-01', 10000, 2000, 1800, 3000, 7200.0, 100)
        """
    )
    # Day 2: 10,500 followers, 3,000 views, 150 engagement
    await execute(
        """
        INSERT INTO account_daily_metrics (
            platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
        ) VALUES ('youtube', '2026-10-02', 10500, 3000, 2700, 4500, 10800.0, 150)
        """
    )

    kpi = await get_kpi_summary(range_key="custom", platform_id="youtube", start_custom="2026-10-01", end_custom="2026-10-02")
    
    # Total Views = 2000 + 3000 = 5000
    assert kpi["total_views"] == 5000
    assert kpi["total_views_formatted"] == "5,0 rb"
    
    # Total Engagement = 100 + 150 = 250
    assert kpi["total_engagement"] == 250
    
    # ER = 250 / 5000 * 100 = 5.0%
    assert kpi["engagement_rate"] == 5.0

    # Total Watch Time = (7200 + 10800) / 3600 = 5.0 hours
    assert kpi["total_watch_time_hours"] == 5.0

    # Followers Growth: latest 10500 - earliest 10000 = +500 (+5.0%)
    assert kpi["followers_growth_abs"] == 500
    assert kpi["followers_growth_pct"] == 5.0


@pytest.mark.asyncio
async def test_kpi_targets_progress_colors():
    await execute("DELETE FROM kpi_targets")
    import uuid
    from datetime import date, timedelta
    today_str = date.today().strftime("%Y-%m-%d")
    tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    # Insert target for views with 10,000 target
    target_id = str(uuid.uuid4())
    await execute(
        """
        INSERT INTO kpi_targets (id, platform_id, metric, period, target_value, start_date, end_date)
        VALUES (?, 'youtube', 'views', 'monthly', 10000.0, ?, ?)
        """,
        [target_id, today_str, tomorrow_str]
    )

    # Insert current metrics: 11,000 views (110% -> green)
    await execute(
        """
        INSERT INTO account_daily_metrics (
            platform_id, date, followers, views, reach, impressions, watch_time_sec, engagement_count
        ) VALUES ('youtube', ?, 5000, 11000, 9000, 15000, 5000.0, 500)
        ON CONFLICT(platform_id, date) DO UPDATE SET views = excluded.views
        """,
        [today_str]
    )

    progress = await get_kpi_targets_progress(platform_id="youtube")
    assert len(progress) == 1
    t = progress[0]
    assert t["percentage"] >= 100.0
    assert "bg-emerald-500" in t["color"]
    assert t["status_text"] == "Tercapai"
