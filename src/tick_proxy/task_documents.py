"""Deterministic document operations for task title, content, and description."""

from __future__ import annotations

from difflib import unified_diff
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, PositiveInt

DOCUMENT_FIELDS = ("title", "content", "desc")


class ReplaceOperation(BaseModel):
    """Replace one exact contiguous source-line block.

    Args:
        op (Literal["replace"]): Fixed operation name.
        old_str (str): Exact current text occupying `old_lines`.
        old_lines (list[PositiveInt]): Contiguous 1-based current line numbers.
        new_str (str): Replacement text.

    Examples:
        >>> ReplaceOperation(op="replace", old_str="old", old_lines=[1], new_str="new").new_str
        'new'
        >>> ReplaceOperation(op="replace", old_str="a\nb", old_lines=[3, 4], new_str="c").old_lines
        [3, 4]
        >>> ReplaceOperation(op="replace", old_str="A", old_lines=[7], new_str="B").op
        'replace'
        >>> ReplaceOperation(op="replace", old_str="", old_lines=[1], new_str="Title").old_str
        ''
    """

    op: Literal["replace"]
    old_str: str
    old_lines: list[PositiveInt]
    new_str: str


class InsertOperation(BaseModel):
    """Insert text after an exact contiguous source-line anchor.

    `insert_lines=[0]` is the sole empty-document/prepend anchor. Otherwise the
    inserted text follows the final listed source line.

    Args:
        op (Literal["insert"]): Fixed operation name.
        insert_lines (list[int]): `[0]` or contiguous 1-based anchor lines.
        insert_text (str): One inserted text block.

    Examples:
        >>> InsertOperation(op="insert", insert_lines=[0], insert_text="Title").insert_text
        'Title'
        >>> InsertOperation(op="insert", insert_lines=[4], insert_text="new line").insert_lines
        [4]
        >>> InsertOperation(op="insert", insert_lines=[2, 3], insert_text="After block").op
        'insert'
        >>> InsertOperation(op="insert", insert_lines=[0], insert_text="").insert_text
        ''
    """

    op: Literal["insert"]
    insert_lines: list[int]
    insert_text: str


DocumentOperation = Annotated[
    ReplaceOperation | InsertOperation, Field(discriminator="op")
]


class DocumentOperations(BaseModel):
    """The three explicit operation lists carried by task create/update payloads.

    Examples:
        >>> DocumentOperations().title_ops
        []
        >>> DocumentOperations(content_ops=[{"op":"insert","insert_lines":[0],"insert_text":"Body"}]).content_ops[0].op
        'insert'
        >>> DocumentOperations(desc_ops=[{"op":"insert","insert_lines":[0],"insert_text":"Desc"}]).desc_ops[0].insert_text
        'Desc'
        >>> len(DocumentOperations(title_ops=[], content_ops=[], desc_ops=[]).content_ops)
        0
    """

    title_ops: list[DocumentOperation] = Field(default_factory=list)
    content_ops: list[DocumentOperation] = Field(default_factory=list)
    desc_ops: list[DocumentOperation] = Field(default_factory=list)


def _contiguous(lines: list[int]) -> bool:
    """Return whether selected line numbers are strictly contiguous.

    Args:
        lines (list[int]): Candidate source line numbers.

    Returns:
        bool: True for a non-empty consecutive sequence.

    Examples:
        >>> _contiguous([2, 3, 4])
        True
        >>> _contiguous([2, 4])
        False
        >>> _contiguous([0])
        True
        >>> _contiguous([])
        False
    """
    return bool(lines) and lines == list(range(lines[0], lines[-1] + 1))


def apply_operations(
    original: str, operations: list[DocumentOperation], field: str
) -> str:
    """Apply checked operations in order, rejecting stale text or line selections.

    Args:
        original (str): Current remote text.
        operations (list[DocumentOperation]): Ordered proposed edits.
        field (str): Field name used in precise validation errors.

    Returns:
        str: Final deterministic text before optional HITL editor adjustments.

    Raises:
        ValueError: If any required line range, exact old text, or insertion
            anchor is invalid in the state produced by earlier operations.

    Examples:
        >>> apply_operations("old", [ReplaceOperation(op="replace", old_str="old", old_lines=[1], new_str="new")], "title")
        'new'
        >>> apply_operations("", [InsertOperation(op="insert", insert_lines=[0], insert_text="new")], "content")
        'new'
        >>> apply_operations("A\nB", [InsertOperation(op="insert", insert_lines=[1], insert_text="X")], "content")
        'A\nX\nB'
        >>> apply_operations("A\nB", [ReplaceOperation(op="replace", old_str="A\nB", old_lines=[1, 2], new_str="C")], "desc")
        'C'
    """
    text = original
    for index, operation in enumerate(operations, start=1):
        lines = text.splitlines()
        if isinstance(operation, ReplaceOperation):
            selected = [int(line) for line in operation.old_lines]
            if not _contiguous(selected) or selected[-1] > len(lines):
                raise ValueError(
                    f"{field}_ops[{index}] old_lines are not a valid contiguous range."
                )
            actual = "\n".join(lines[selected[0] - 1 : selected[-1]])
            if actual != operation.old_str:
                raise ValueError(
                    f"{field}_ops[{index}] old_str does not exactly match old_lines. Read the task again."
                )
            replacement = operation.new_str.splitlines()
            lines[selected[0] - 1 : selected[-1]] = replacement
            text = "\n".join(lines)
        else:
            anchors = operation.insert_lines
            if anchors == [0]:
                text = (
                    operation.insert_text
                    + ("\n" if text and operation.insert_text else "")
                    + text
                )
                continue
            if not _contiguous(anchors) or anchors[0] < 1 or anchors[-1] > len(lines):
                raise ValueError(
                    f"{field}_ops[{index}] insert_lines are not a valid contiguous anchor."
                )
            insert_at = anchors[-1]
            lines[insert_at:insert_at] = operation.insert_text.splitlines()
            text = "\n".join(lines)
    return text


def prepare_document_review(
    original: dict[str, Any], operations: DocumentOperations, require_title: bool
) -> dict[str, dict[str, str]]:
    """Preflight all operation lists before HITL and produce the three final candidates.

    Args:
        original (dict[str, Any]): Existing task document fields, or `{}` on create.
        operations (DocumentOperations): Explicit agent operations for every field.
        require_title (bool): True for task creation.

    Returns:
        dict[str, dict[str, str]]: `original` and operation-derived `proposed` texts.

    Raises:
        ValueError: If operations are stale or a created title stays empty.

    Examples:
        >>> prepare_document_review({}, DocumentOperations(title_ops=[{"op":"insert","insert_lines":[0],"insert_text":"T"}]), True)["proposed"]["title"]
        'T'
        >>> prepare_document_review({"content":"A"}, DocumentOperations(), False)["original"]["content"]
        'A'
        >>> prepare_document_review({"title":"A"}, DocumentOperations(title_ops=[{"op":"replace","old_str":"A","old_lines":[1],"new_str":"B"}]), False)["proposed"]["title"]
        'B'
        >>> prepare_document_review({}, DocumentOperations(content_ops=[{"op":"insert","insert_lines":[0],"insert_text":"B"}]), False)["proposed"]["content"]
        'B'
    """
    original_text = {field: str(original.get(field) or "") for field in DOCUMENT_FIELDS}
    proposed = {
        "title": apply_operations(
            original_text["title"], operations.title_ops, "title"
        ),
        "content": apply_operations(
            original_text["content"], operations.content_ops, "content"
        ),
        "desc": apply_operations(original_text["desc"], operations.desc_ops, "desc"),
    }
    if require_title and not proposed["title"].strip():
        raise ValueError("title_ops must produce a non-empty title for task-create.")
    return {"original": original_text, "proposed": proposed}


def materialize_document_fields(
    original: dict[str, Any], payload: dict[str, Any], require_title: bool
) -> dict[str, Any]:
    """Return one payload whose document fields are derived only from operations.

    Args:
        original (dict[str, Any]): Current task fields, or `{}` for a new task.
        payload (dict[str, Any]): Normal action payload carrying the three op lists.
        require_title (bool): Whether the final title must be non-empty.

    Returns:
        dict[str, Any]: Original payload plus the materialized title/content/desc.

    Examples:
        >>> materialize_document_fields({}, {"title_ops":[{"op":"insert","insert_lines":[0],"insert_text":"T"}]}, True)["title"]
        'T'
        >>> materialize_document_fields({"content":"A"}, {"content_ops":[{"op":"replace","old_str":"A","old_lines":[1],"new_str":"B"}]}, False)["content"]
        'B'
        >>> materialize_document_fields({}, {"desc_ops":[{"op":"insert","insert_lines":[0],"insert_text":"D"}]}, False)["desc"]
        'D'
        >>> materialize_document_fields({"title":"A"}, {}, False)["title"]
        'A'
    """
    document = prepare_document_review(
        original,
        DocumentOperations.model_validate(payload),
        require_title=require_title,
    )
    return {**payload, **document["proposed"]}


def field_diffs(original: dict[str, str], submitted: dict[str, str]) -> dict[str, str]:
    """Build independent final inline patches for each task document field.

    Args:
        original (dict[str, str]): Remote text before review.
        submitted (dict[str, str]): Exact text submitted after HITL edits.

    Returns:
        dict[str, str]: `title_diff`, `content_diff`, and `desc_diff`.

    Examples:
        >>> field_diffs({"title":"A","content":"","desc":""}, {"title":"B","content":"","desc":""})["title_diff"].splitlines()[0]
        '--- title/original'
        >>> field_diffs({"title":"A","content":"","desc":""}, {"title":"A","content":"","desc":""})["content_diff"]
        ''
        >>> "-A" in field_diffs({"title":"","content":"A","desc":""}, {"title":"","content":"B","desc":""})["content_diff"]
        True
        >>> "+B" in field_diffs({"title":"","content":"","desc":"A"}, {"title":"","content":"","desc":"B"})["desc_diff"]
        True
    """
    return {
        f"{field}_diff": "".join(
            unified_diff(
                original[field].splitlines(keepends=True),
                submitted[field].splitlines(keepends=True),
                fromfile=f"{field}/original",
                tofile=f"{field}/submitted",
            )
        )
        for field in DOCUMENT_FIELDS
    }
