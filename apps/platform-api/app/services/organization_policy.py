"""Stable organization ownership rules used by member and class operations."""

from __future__ import annotations


SUZHOU_ROOT_ORG_UNIT_ID = "org-suzhou"
DIRECT_CLASS_NAMES = frozenset({"先锋班", "神仙班", "黄埔一班", "黄埔二班"})


def is_suzhou_direct_class(*, class_name: str | None, parent_id: str | None) -> bool:
    """Only the confirmed four classes may operate directly under 苏州塾."""
    return (
        parent_id == SUZHOU_ROOT_ORG_UNIT_ID
        and str(class_name or "").strip() in DIRECT_CLASS_NAMES
    )


def is_valid_member_class_parent(
    *, class_name: str | None, parent_id: str | None, member_center_id: str
) -> bool:
    """A learner may study in their center class or one of the four direct classes."""
    return parent_id == member_center_id or is_suzhou_direct_class(
        class_name=class_name, parent_id=parent_id
    )
