"""Habit actions — list, sections, create, update (full replacement), delete, check-in, records."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from ..models import Verification
from .base import ActionDef, always_verify, compare


class EmptyPayload(BaseModel):
    pass


def habit_list(client: TickClient, p: EmptyPayload) -> list[dict]:
    """List every habit with its streaks and totals.

    Parameters:
        - (no payload)

    Examples:
        - List habits:
            `tick-proxy do habit-list`
            → [{"id":"65f1","name":"Corde à sauter","type":"Boolean","currentStreak":12,"totalCheckIns":48}]
        - Table view:
            `tick-proxy do habit-list -f table`
            → [{"id":"65f1","name":"Corde à sauter","currentStreak":12}]
    """
    return client.v2_get("/habits")


def habit_section_list(client: TickClient, p: EmptyPayload) -> list[dict]:
    """List habit sections (Morning / Afternoon / Evening).

    Parameters:
        - (no payload)

    Examples:
        - List sections:
            `tick-proxy do habit-section-list`
            → [{"id":"s1","name":"Morning","sortOrder":0},{"id":"s2","name":"Evening","sortOrder":1}]
        - Use an id in habit-create:
            `tick-proxy do habit-section-list`
            → [{"id":"s1","name":"Morning"}]
    """
    return client.v2_get("/habitSections")


class HabitCreatePayload(BaseModel):
    name: str = Field(..., description="Habit name")
    habit_type: str = Field("Boolean", description="Boolean | Real")
    goal: float | None = Field(None, description="Target value for Real habits")
    step: float | None = Field(None, description="Increment step for Real habits")
    unit: str | None = Field(None, description="Unit, e.g. L, pages, min")
    color: str | None = Field(None, description="Hex color, e.g. #4DB6AC")
    section_id: str | None = Field(None, description="Section id (habit-section-list)")
    repeat_rule: str | None = Field(None, description="RRULE; default daily")
    reminders: list[str] | None = Field(None, description='["08:00","20:00"]')
    encouragement: str | None = Field(None, description="Message shown on completion")


def habit_create(client: TickClient, p: HabitCreatePayload) -> dict:
    """Create a habit (Boolean done/not-done, or Real measurable).

    Parameters:
        - name (str): Habit name. habit_type (str): Boolean (default) or Real.
        - goal/step (float|null) + unit (str|null): required for Real habits.
        - color (str|null), section_id (str|null), repeat_rule (str|null RRULE).
        - reminders (list[str]|null): times, e.g. ["08:00","20:00"].
        - encouragement (str|null): completion message.

    Examples:
        - Boolean habit:
            `tick-proxy do habit-create '{"name":"Corde à sauter","color":"#4DB6AC"}'`
            → {"id2etag":{"65f1":"abc"},"id2error":{}}
        - Measurable habit:
            `tick-proxy do habit-create '{"name":"Lecture","habit_type":"Real","goal":30,"step":5,"unit":"pages"}'`
            → {"id2etag":{"65f2":"def"},"id2error":{}}
    """
    habit: dict[str, Any] = {
        "name": p.name,
        "type": p.habit_type,
        "goal": p.goal if p.goal is not None else 1,
        "step": p.step if p.step is not None else 1,
        "status": 0,
    }
    for src, dst in (
        ("unit", "unit"),
        ("color", "color"),
        ("section_id", "sectionId"),
        ("repeat_rule", "repeatRule"),
        ("reminders", "reminders"),
        ("encouragement", "encouragement"),
    ):
        v = getattr(p, src)
        if v is not None:
            habit[dst] = v
    return client.v2_post("/habits/batch", {"add": [habit]})


class HabitUpdatePayload(BaseModel):
    habit_id: str = Field(..., description="Habit id")
    name: str | None = Field(None, description="New name")
    goal: float | None = Field(None, description="New target value")
    step: float | None = Field(None, description="New increment step")
    unit: str | None = Field(None, description="New unit")
    color: str | None = Field(None, description="New hex color")
    status: int | None = Field(None, description="0=active 2=archived")
    section_id: str | None = Field(None, description="Move to another section")
    repeat_rule: str | None = Field(None, description="New RRULE")
    reminders: list[str] | None = Field(None, description="[] clears all reminders")
    encouragement: str | None = Field(None, description="New completion message")


@always_verify("name")
def habit_update(
    client: TickClient, p: HabitUpdatePayload
) -> tuple[dict, Verification]:
    """Update a habit — read-modify-write, then verified.

    V2 `/habits/batch` is a FULL REPLACEMENT: sending only the fields you want
    to change wipes name, color, status and everything else. This action always
    fetches the current habit first, merges your changes, and then reads the
    result back to prove nothing was wiped.

    Parameters:
        - habit_id (str): The habit to update.
        - name/goal/step/unit/color/status/section_id/repeat_rule/encouragement.
        - reminders (list[str]|null): pass [] to clear all reminders.

    Examples:
        - Add reminders:
            `tick-proxy do habit-update '{"habit_id":"65f1","reminders":["08:00","20:00"]}'`
            → {"id":"65f1","name":"Corde à sauter","reminders":["08:00","20:00"]}
        - Archive a habit:
            `tick-proxy do habit-update '{"habit_id":"65f1","status":2}'`
            → {"id":"65f1","name":"Corde à sauter","status":2}
    """
    habits = client.v2_get("/habits")
    current = next((h for h in habits if h.get("id") == p.habit_id), None)
    if current is None:
        raise ValueError(f"Habit not found: {p.habit_id}")

    merged = dict(current)
    for src, dst in (
        ("name", "name"),
        ("goal", "goal"),
        ("step", "step"),
        ("unit", "unit"),
        ("color", "color"),
        ("status", "status"),
        ("section_id", "sectionId"),
        ("repeat_rule", "repeatRule"),
        ("reminders", "reminders"),
        ("encouragement", "encouragement"),
    ):
        v = getattr(p, src)
        if v is not None:
            merged[dst] = v
    client.v2_post("/habits/batch", {"update": [merged]})

    after = next((h for h in client.v2_get("/habits") if h.get("id") == p.habit_id), {})
    verification = compare(
        "GET /api/v2/habits",
        {"name": merged.get("name")},
        {"name": after.get("name")},
    )
    return after, verification


class HabitIdPayload(BaseModel):
    habit_id: str = Field(..., description="Habit id")


def habit_delete(client: TickClient, p: HabitIdPayload) -> dict:
    """Delete a habit permanently. IRREVERSIBLE — HITL required.

    Parameters:
        - habit_id (str): The habit to delete.

    Examples:
        - Delete a habit:
            `tick-proxy do habit-delete '{"habit_id":"65f1"}'`
            → {"deleted":"65f1"}
        - Delete a test habit:
            `tick-proxy do habit-delete '{"habit_id":"65f9"}'`
            → {"deleted":"65f9"}
    """
    client.v2_post("/habits/batch", {"delete": [{"id": p.habit_id}]})
    return {"deleted": p.habit_id}


class HabitCheckinPayload(BaseModel):
    habit_id: str = Field(..., description="Habit id")
    checkin_stamp: int = Field(..., description="Date as YYYYMMDD, e.g. 20260809")
    value: float | None = Field(None, description="Value for Real habits")
    status: int = Field(2, description="0=unchecked 2=completed")


def habit_checkin(client: TickClient, p: HabitCheckinPayload) -> dict:
    """Record a habit check-in for a given day.

    Parameters:
        - habit_id (str): The habit. checkin_stamp (int): YYYYMMDD.
        - value (float|null): amount for Real habits (omit for Boolean).
        - status (int): 2=completed (default), 0=unchecked.

    Examples:
        - Tick a boolean habit today:
            `tick-proxy do habit-checkin '{"habit_id":"65f1","checkin_stamp":20260809}'`
            → {"id2etag":{"r1":"abc"},"id2error":{}}
        - Log 1.5 L of water:
            `tick-proxy do habit-checkin '{"habit_id":"65f2","checkin_stamp":20260809,"value":1.5}'`
            → {"id2etag":{"r2":"def"},"id2error":{}}
    """
    record: dict[str, Any] = {
        "habitId": p.habit_id,
        "checkinStamp": p.checkin_stamp,
        "status": p.status,
    }
    if p.value is not None:
        record["value"] = p.value
    return client.v2_post("/habitCheckins/batch", {"add": [record]})


class HabitRecordsPayload(BaseModel):
    habit_ids: list[str] = Field(..., description="Habit ids to query")
    after_stamp: int = Field(0, description="Only after this YYYYMMDD (0 = all)")


def habit_records(client: TickClient, p: HabitRecordsPayload) -> dict:
    """Check-in history for one or more habits.

    Parameters:
        - habit_ids (list[str]): The habits to query.
        - after_stamp (int): Only return check-ins after this YYYYMMDD (0 = all).

    Examples:
        - Full history of one habit:
            `tick-proxy do habit-records '{"habit_ids":["65f1"]}'`
            → {"checkins":{"65f1":[{"checkinStamp":20260809,"status":2}]}}
        - Since August only:
            `tick-proxy do habit-records '{"habit_ids":["65f1"],"after_stamp":20260801}'`
            → {"checkins":{"65f1":[{"checkinStamp":20260809,"status":2}]}}
    """
    return client.v2_post(
        "/habitCheckins/query",
        {"habitIds": p.habit_ids, "afterStamp": p.after_stamp},
    )


ACTIONS = [
    ActionDef("habit-list", EmptyPayload, habit_list, v2=True, group="Habits"),
    ActionDef(
        "habit-section-list", EmptyPayload, habit_section_list, v2=True, group="Habits"
    ),
    ActionDef(
        "habit-create", HabitCreatePayload, habit_create, v2=True, group="Habits"
    ),
    ActionDef(
        "habit-update",
        HabitUpdatePayload,
        habit_update,
        verify="always",
        v2=True,
        group="Habits",
    ),
    ActionDef(
        "habit-delete", HabitIdPayload, habit_delete, hitl=True, v2=True, group="Habits"
    ),
    ActionDef(
        "habit-checkin", HabitCheckinPayload, habit_checkin, v2=True, group="Habits"
    ),
    ActionDef(
        "habit-records", HabitRecordsPayload, habit_records, v2=True, group="Habits"
    ),
]
