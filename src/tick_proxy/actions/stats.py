"""Stats actions — focus/pomodoro statistics, account status, productivity score."""

from pydantic import BaseModel, Field

from ..client import TickClient
from .base import ActionDef


class EmptyPayload(BaseModel):
    pass


class FocusStatsPayload(BaseModel):
    from_date: str = Field(..., description="Start date YYYYMMDD, e.g. 20260801")
    to_date: str = Field(..., description="End date YYYYMMDD, e.g. 20260809")
    stat_type: str = Field("heatmap", description="heatmap | distribution")


def focus_stats(client: TickClient, p: FocusStatsPayload) -> dict:
    """Focus / pomodoro statistics over a date range.

    Parameters:
        - from_date (str): YYYYMMDD start. to_date (str): YYYYMMDD end.
        - stat_type (str): `heatmap` (daily durations) or `distribution`
          (per-tag breakdown).

    Examples:
        - Daily focus heatmap:
            `tick-proxy do focus-stats '{"from_date":"20260801","to_date":"20260809"}'`
            → [{"day":20260809,"duration":5400}]
        - Per-tag distribution:
            `tick-proxy do focus-stats '{"from_date":"20260801","to_date":"20260809","stat_type":"distribution"}'`
            → {"tagDurations":{"work":7200,"revision":3600}}
    """
    endpoint = (
        f"/pomodoro/statistics/heatmap/{p.from_date}/{p.to_date}"
        if p.stat_type == "heatmap"
        else f"/pomodoro/statistics/dist/{p.from_date}/{p.to_date}"
    )
    return client.v2_get(endpoint)


def user_status(client: TickClient, p: EmptyPayload) -> dict:
    """Account status — inbox id, Pro subscription, team membership.

    Parameters:
        - (no payload)

    Examples:
        - Who am I?
            `tick-proxy do user-status`
            → {"userId":"5f8a1c2e4b7d4e9f8a1b2c3d","username":"user@example.com","inboxId":"inbox1275839472","pro":true}
        - Check Pro expiry:
            `tick-proxy do user-status`
            → {"pro":true,"proEndDate":"2027-01-15T00:00:00.000+0000"}
    """
    return client.v2_get("/user/status")


def user_stats(client: TickClient, p: EmptyPayload) -> dict:
    """Productivity statistics — score, level, streaks, completion counts.

    Parameters:
        - (no payload)

    Examples:
        - Today's productivity:
            `tick-proxy do user-stats`
            → {"score":15420,"level":8,"completedToday":6,"completedThisWeek":31,"currentStreak":12}
        - Streak check:
            `tick-proxy do user-stats`
            → {"currentStreak":12,"maxStreak":47}
    """
    return client.v2_get("/statistics/general")


ACTIONS = [
    ActionDef("focus-stats", FocusStatsPayload, focus_stats, v2=True, group="Stats"),
    ActionDef("user-status", EmptyPayload, user_status, v2=True, group="Stats"),
    ActionDef("user-stats", EmptyPayload, user_stats, v2=True, group="Stats"),
]
