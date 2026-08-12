"""The 52 `do` actions — one `ActionDef` per action, grouped by domain."""

from .base import ActionDef, compare, require_verification
from .registry import REGISTRY, by_group, get

__all__ = [
    "REGISTRY",
    "ActionDef",
    "by_group",
    "compare",
    "get",
    "require_verification",
]
