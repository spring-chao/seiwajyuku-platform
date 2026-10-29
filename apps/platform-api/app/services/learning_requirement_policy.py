"""Requirement exemptions from canonical study groups, never attendance deletion."""
from __future__ import annotations

import re
import unicodedata

METRICS = frozenset({"class_meeting", "group_meeting", "reading", "sharing"})

def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))

def exemption_reason(group_name, class_name="", *, metric):
    if metric not in METRICS:
        raise ValueError("Unknown learning requirement metric")
    name, klass = compact(group_name), compact(class_name)
    # A bare 精进组 is an ordinary group. Never manufacture a class prefix
    # from the separate class_name field displayed beside the group name.
    if re.fullmatch(r".+班精进组", name):
        return "带班级前缀的精进组免学习要求，不计入参与率分子或分母"
    if metric == "group_meeting" and (name == "班委组" or klass and name == klass + "班委组"):
        return "班委组不纳入小组学习会应开及参会率要求"
    return ""

def requirement_roster(connection, *, metric, **filters):
    from app.services.study_membership import group_roster
    return [row for row in group_roster(connection, **filters)
            if not exemption_reason(row.get("group_name"), row.get("class_name"), metric=metric)]

def excluded_members(connection, member_ids, *, metric):
    from app.services.study_membership import group_roster
    result = {}
    ids = sorted(set(int(mid) for mid in member_ids))
    for start in range(0, len(ids), 200):
        for row in group_roster(connection, member_ids=ids[start:start+200], include_ungrouped=True):
            reason = exemption_reason(row.get("group_name"), row.get("class_name"), metric=metric)
            if reason:
                result[int(row["member_id"])] = reason
    return result
