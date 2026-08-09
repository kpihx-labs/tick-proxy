"""Registry integrity — the anti-drift gate for the 52 actions."""

from tick_proxy.actions.registry import REGISTRY, by_group

EXPECTED_ACTIONS = 52
ALWAYS_VERIFY = {
    "task-move",
    "task-parent-set",
    "subtask-create",
    "project-create",
    "project-update",
    "habit-update",
}
HITL = {
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


def test_always_verify_actions_carry_the_decorator():
    declared = {n for n, a in REGISTRY.items() if a.verify == "always"}
    assert declared == ALWAYS_VERIFY
    for name in declared:
        handler = REGISTRY[name].handler
        assert getattr(handler, "__always_verify__", False), (
            f"{name} declares verify='always' but lacks @always_verify"
        )


def test_hitl_actions_are_the_destructive_ones():
    assert {n for n, a in REGISTRY.items() if a.hitl} == HITL


def test_groups_cover_every_action():
    assert sum(len(v) for v in by_group().values()) == EXPECTED_ACTIONS
