"""Class teachers and monitors can confirm a scoped class meeting roster."""
from app.db import transaction
from app.services import signin_engine
from app.services.study_membership import group_roster
from app.services.volunteer_positions import CLASS_ATTENDANCE_VIEW, get_member_volunteer_services


POSITIONS = frozenset({"volunteer_class_counselor", "volunteer_class_monitor"})


def class_ids(services: dict) -> list[str]:
    if services.get("needs_manual_review"):
        return []
    return sorted({
        role["scope_org_unit_id"]
        for role in services.get("roles", [])
        if role.get("position_key") in POSITIONS
        and CLASS_ATTENDANCE_VIEW in role.get("capabilities", [])
        and role.get("scope_org_unit_id")
    })


def request(actor: dict, action: str, *, class_id=None, event_id=None,
            target_id=None, konpa_choice=None) -> dict:
    signin_engine.enabled(member=True, write=action == "confirm")
    actor_id = actor["member_id"]
    allowed = class_ids(get_member_volunteer_services(actor_id))
    if not allowed or (class_id is not None and class_id not in allowed):
        raise PermissionError("仅班主任和班长可确认所负责班级的到场")
    payload = {"action": action, "class_ids": allowed, "actor_id": str(actor_id)}
    if class_id is not None:
        with transaction() as connection:
            members = group_roster(
                connection, class_id=class_id, include_ungrouped=True,
                member_ids=[target_id] if target_id is not None else None,
            )
        member_ids = {str(row["member_id"]) for row in members} | {str(actor_id)}
        if target_id is not None and str(target_id) not in member_ids:
            raise PermissionError("该学长不在所负责班级的当前名单内")
        payload.update(class_id=class_id, event_id=event_id, member_ids=sorted(member_ids))
        payload["actor_member"] = {
            key: actor[key] for key in ("name", "member_code", "home_class_org_unit_id", "class_name", "group_name")
        }
        if target_id is not None:
            payload.update(target_id=str(target_id), konpa_choice=konpa_choice)
    return signin_engine.engine_request("/ops/v1/class-arrival", payload, timeout=15)
