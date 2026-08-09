"""Filter engine — the core of every query-* and view-* action."""

from tick_proxy.query import (
    day_bounds,
    filter_tasks,
    is_timed,
    parse_dt,
    resolve_project_ids,
    task_tags,
    text_matches,
)

TASKS = [
    {
        "id": "1",
        "title": "Ship v1",
        "projectId": "6xxx",
        "priority": 5,
        "tags": ["Work"],
        "dueDate": "2026-08-09T21:00:00.000+0000",
        "modifiedTime": "2026-08-09T10:00:00.000+0000",
    },
    {
        "id": "2",
        "title": "Read a book",
        "projectId": "6yyy",
        "priority": 0,
        "tags": ["leisure"],
        "dueDate": "2026-08-20T00:00:00.000+0000",
        "isAllDay": True,
        "modifiedTime": "2026-06-01T10:00:00.000+0000",
    },
    {"id": "3", "title": "No date", "projectId": "6xxx", "priority": 3},
]


def test_parse_dt_handles_ticktick_and_iso():
    assert parse_dt("2026-08-09T21:00:00.000+0000").hour == 21
    assert parse_dt("2026-08-09").date().isoformat() == "2026-08-09"
    assert parse_dt(None) is None


def test_day_bounds_is_a_single_day():
    start, end = day_bounds("2026-08-09")
    assert start.date().isoformat() == "2026-08-09"
    assert (end - start).days == 0


def test_task_tags_lowercases():
    assert task_tags({"tags": ["Work", "EXAM"]}) == ["work", "exam"]
    assert task_tags({}) == []


def test_is_timed_excludes_all_day():
    assert is_timed(TASKS[0]) is True
    assert is_timed(TASKS[1]) is False


def test_text_matches_modes():
    assert text_matches(TASKS[0], "ship") is True
    assert text_matches(TASKS[0], "ship missing", mode="all") is False
    assert text_matches(TASKS[0], None) is True


def test_filter_by_priority_and_project():
    assert [t["id"] for t in filter_tasks(TASKS, {"min_priority": 3})] == ["1", "3"]
    assert [t["id"] for t in filter_tasks(TASKS, {"project_ids": ["6yyy"]})] == ["2"]


def test_filter_by_tags_any_and_all():
    assert [t["id"] for t in filter_tasks(TASKS, {"tags": ["work"]})] == ["1"]
    assert filter_tasks(TASKS, {"tags": ["work", "leisure"], "tag_mode": "all"}) == []


def test_filter_by_due_window():
    got = filter_tasks(TASKS, {"due_from": "2026-08-09", "due_to": "2026-08-09T23:59:59"})
    assert [t["id"] for t in got] == ["1"]


def test_filter_timed_only_and_stale():
    assert [t["id"] for t in filter_tasks(TASKS, {"timed_only": True})] == ["1"]
    stale = filter_tasks(TASKS, {"modified_to": "2026-07-01"})
    assert [t["id"] for t in stale] == ["2"]


def test_filter_limit_and_sort():
    got = filter_tasks(TASKS, {"sort_by": "priority", "descending": True, "limit": 1})
    assert len(got) == 1


def test_resolve_project_ids_by_name():
    projects = [{"id": "6xxx", "name": "🛠️ Tech & Science"}, {"id": "6yyy", "name": "Books"}]
    assert resolve_project_ids({"project_names": ["tech"]}, projects) == ["6xxx"]
    assert resolve_project_ids({}, projects) == []
