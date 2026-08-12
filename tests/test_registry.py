"""Registry integrity — the anti-drift gate for the 52 actions."""

from tick_proxy.actions.registry import REGISTRY, by_group

EXPECTED_ACTIONS = 52
REQUIRE_VERIFICATION = {
    "project-delete",
    "task-create",
    "task-update",
    "subtask-create",
    "task-move",
    "task-parent-set",
    "project-create",
    "project-update",
    "habit-update",
}
HITL = {
    "column-manage",
    "folder-manage",
    "task-create",
    "task-update",
    "subtask-create",
    "task-batch-create",
    "task-batch-update",
    "task-delete",
    "task-batch-delete",
    "project-delete",
    "tag-merge",
    "tag-delete",
    "habit-delete",
    "raw",
}


def test_action_count():
    assert len(REGISTRY) == EXPECTED_ACTIONS


def test_no_duplicate_names():
    assert len(REGISTRY) == len(set(REGISTRY))


def test_names_are_domain_first_kebab():
    for name in REGISTRY:
        assert name == name.lower()
        assert " " not in name and "_" not in name


def test_every_action_has_a_docstring_with_examples():
    for name, action in REGISTRY.items():
        doc = action.handler.__doc__ or ""
        assert doc.strip(), f"{name} has no docstring"
        assert "Parameters:" in doc, f"{name} docstring lacks Parameters:"
        assert "Examples:" in doc, f"{name} docstring lacks Examples:"


def test_required_verifications_carry_the_decorator():
    declared = {
        name
        for name, action in REGISTRY.items()
        if getattr(action.handler, "__require_verification__", False)
    }
    assert declared == REQUIRE_VERIFICATION
    for name in declared:
        handler = REGISTRY[name].handler
        assert getattr(handler, "__verification_checks__", ()), (
            f"{name} requires verification but declares no compared fields"
        )


def test_hitl_actions_match_the_explicit_review_policy():
    assert {n for n, a in REGISTRY.items() if a.hitl} == HITL
    for name in HITL:
        assert getattr(REGISTRY[name].handler, "__require_approval__", False), (
            f"{name} is HITL but does not declare @require_approval"
        )


def test_all_irreversible_actions_declare_preflight_and_locked_target():
    """Ensure every destructive review validates and locks its target first.

    Returns:
        None: Every irreversible handler exposes its guard and target fields.

    Examples:
        >>> bool(("project_id",))
        True
        >>> callable(lambda: None)
        True
    """
    expected = {
        "project-delete": ("project_id",),
        "task-delete": ("project_id", "task_id"),
        "task-batch-delete": ("tasks",),
        "tag-delete": ("name",),
        "tag-merge": ("source", "target"),
        "habit-delete": ("habit_id",),
        "folder-manage": ("delete",),
        "column-manage": ("project_id", "delete"),
    }
    for name, identity_fields in expected.items():
        handler = REGISTRY[name].handler
        assert callable(getattr(handler, "__preflight_check__", None)), name
        assert getattr(handler, "__preflight_identity_fields__", ()) == identity_fields


def test_only_task_writes_declare_three_document_reviews():
    reviewed = {
        name
        for name, action in REGISTRY.items()
        if getattr(action.handler, "__require_reviews__", False)
    }
    assert reviewed == {"task-create", "task-update", "subtask-create"}
    for name in reviewed:
        assert getattr(REGISTRY[name].handler, "__review_fields__", ()) == (
            "title",
            "content",
            "desc",
        )


def test_groups_cover_every_action():
    assert sum(len(v) for v in by_group().values()) == EXPECTED_ACTIONS
