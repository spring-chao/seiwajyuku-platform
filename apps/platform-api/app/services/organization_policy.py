"""Stable organization ownership rules used by member and class operations."""

from __future__ import annotations


SUZHOU_ROOT_ORG_UNIT_ID = "org-suzhou"
DIRECT_CLASS_NAMES = frozenset({"先锋班", "神仙班", "黄埔一班", "黄埔二班"})
WUXI_ROOT_ORG_UNIT_ID = "org-wuxi"
WUXI_GUIDANCE_ORG_UNIT_IDS = frozenset(
    {"org-wuxi-guidance-1", "org-wuxi-guidance-2"}
)


def is_valid_member_primary_org(
    *, org_unit_id: str, unit_type: str | None, parent_id: str | None = None
) -> bool:
    """Return whether an organization can own a learner master record.

    Regional centers remain the normal learner owner.  Wuxi deliberately uses
    two ``OPERATING_UNIT`` guidance groups instead of regional-center nodes;
    only those two confirmed nodes are allowed.  This keeps arbitrary
    operating units, classes and groups from becoming learner owners.
    """
    normalized_type = str(unit_type or "").upper()
    if normalized_type == "REGIONAL_CENTER":
        return True
    return (
        normalized_type == "OPERATING_UNIT"
        and org_unit_id in WUXI_GUIDANCE_ORG_UNIT_IDS
        and parent_id == WUXI_ROOT_ORG_UNIT_ID
    )


def is_suzhou_direct_class(*, class_name: str | None, parent_id: str | None) -> bool:
    """Only the confirmed four classes may operate directly under 苏州塾."""
    return (
        parent_id == SUZHOU_ROOT_ORG_UNIT_ID
        and str(class_name or "").strip() in DIRECT_CLASS_NAMES
    )


def is_valid_member_class_parent(
    *, class_name: str | None, parent_id: str | None, member_center_id: str
) -> bool:
    """Validate class ownership for the learner's management unit.

    In addition to classes directly under the learner's management unit and
    the four confirmed Suzhou direct classes, Wuxi's ``精进班`` is a special
    cohort directly under the Wuxi root.  It must not be moved under either
    guidance group merely to make the tree look uniform.
    """
    return parent_id == member_center_id or is_suzhou_direct_class(
        class_name=class_name, parent_id=parent_id
    ) or (
        parent_id == WUXI_ROOT_ORG_UNIT_ID
        and member_center_id in WUXI_GUIDANCE_ORG_UNIT_IDS
        and str(class_name or "").strip() == "精进班"
    )
