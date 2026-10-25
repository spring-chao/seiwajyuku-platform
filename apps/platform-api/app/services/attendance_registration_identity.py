"""Resolve enrollment identities without guessing among same-name members."""
from __future__ import annotations

from app.core.privacy import phone_hash
from app.db import fetch_all
from app.services.study_membership import CURRENT_CLASS_ID_SQL, CURRENT_GROUP_ID_SQL


def attach_current_registration_names(rows: list[dict], *, event_id: str = "") -> None:
    """Project current master names onto already-authorized event rows.

    Registration names and actual-attendee facts remain immutable here. Only
    stable platform IDs/member codes can resolve a rename; names and phones
    are never used to guess a different person in this read path.
    """
    def canonical_id(value):
        text = str(value or "").strip()
        if not text.isascii() or not text.isdigit() or len(text) > 19:
            return None
        number = int(text)
        return str(number) if 0 < number <= 9223372036854775807 else None

    ids, codes, registrations = set(), set(), set()
    selection = (f"m.id,m.member_code,m.name,{CURRENT_CLASS_ID_SQL} AS current_class_id,"
                 f"{CURRENT_GROUP_ID_SQL} AS current_group_id,"
                 f"(SELECT name FROM org_units WHERE id={CURRENT_GROUP_ID_SQL}) AS current_group_name")
    for row in rows:
        row.pop("registered_display_name", None)
        row.pop("registered_name_changed", None)
        for field in ("current_member_id", "current_class_org_unit_id", "current_group_org_unit_id", "current_group_name", "current_membership_resolved"):
            row.pop(field, None)
        member_id = str(row.get("platform_member_id") or "").strip()
        code = str(row.get("member_code") or "").strip()
        if canonical_id(member_id):
            ids.add(int(member_id))
        elif not member_id and code:
            codes.add(code)
        elif not member_id and not code and row.get("registration_id"):
            registrations.add(str(row["registration_id"]))
    by_registration = {}
    if event_id:
        values = sorted(registrations)
        for start in range(0, len(values), 200):
            chunk = values[start:start + 200]
            marks = ",".join("?" for _ in chunk)
            matches = fetch_all(
                f"SELECT DISTINCT r.external_record_id,{selection} FROM attendance_records r "
                "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
                "JOIN attendance_event_groups g ON g.id=s.event_group_id "
                "JOIN members m ON m.id=r.member_id AND m.status='ACTIVE' "
                f"WHERE g.source_key='signin' AND s.external_session_id=? AND r.external_record_id IN ({marks})",
                (event_id, *chunk))
            grouped = {}
            for member in matches:
                grouped.setdefault(str(member["external_record_id"]), []).append(member)
            by_registration.update({key: members[0] for key, members in grouped.items() if len(members) == 1})
    by_id, by_code = {}, {}
    for field, values in (("id", sorted(ids)), ("member_code", sorted(codes))):
        for start in range(0, len(values), 200):
            chunk = values[start:start + 200]
            marks = ",".join("?" for _ in chunk)
            for member in fetch_all(f"SELECT {selection} FROM members m WHERE m.status='ACTIVE' AND m.{field} IN ({marks})", tuple(chunk)):
                by_id[str(member["id"])] = member
                by_code[str(member["member_code"])] = member
    for row in rows:
        member_id = str(row.get("platform_member_id") or "").strip()
        code = str(row.get("member_code") or "").strip()
        member = by_id.get(canonical_id(member_id))
        if not member_id:
            member = by_code.get(code) if code else by_registration.get(str(row.get("registration_id")))
        if not member or code and code != str(member["member_code"]):
            continue
        row["registered_display_name"] = member["name"]
        row["registered_name_changed"] = member["name"] != (row.get("registered_name") or row.get("name"))
        row.update(current_member_id=int(member["id"]), current_class_org_unit_id=member["current_class_id"],
                   current_group_org_unit_id=member["current_group_id"], current_group_name=member["current_group_name"],
                   current_membership_resolved=True)


def registration_identities(rows: list[dict], *, bound_member: dict | None = None) -> list[dict | None]:
    keys = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        try:
            hashed = phone_hash(str(row.get("phone") or "")) if name else None
        except ValueError:
            hashed = None
        keys.append((name, hashed) if hashed else None)
    identities: dict[tuple, list] = {}
    unique = list(dict.fromkeys(key for key in keys if key))
    # Bound parameter counts and database trips for large Excel imports.
    for start in range(0, len(unique), 150):
        batch = unique[start:start + 150]
        clauses = " OR ".join("(name=? AND phone_hash=?)" for _ in batch)
        members = fetch_all(
            "SELECT id,member_code,name,phone_hash FROM members "
            "WHERE status='ACTIVE' AND (" + clauses + ")",
            tuple(value for pair in batch for value in pair),
        )
        for member in members:
            key = (str(member["name"]).strip(), member["phone_hash"])
            identities.setdefault(key, []).append(member)
    result = []
    for key in keys:
        matches = identities.get(key, [])
        result.append({"platform_member_id": str(matches[0]["id"]),
                       "member_code": str(matches[0]["member_code"])}
                      if len(matches) == 1 else None)
    # Import still requires name+phone. For authenticated self check-in the
    # bound ID+code identifies the person, even when other active people have
    # the same name. A name-only enrollment becomes a SELF confirmation, not
    # a claim that a missing/old phone was verified.
    if bound_member:
        name = str(bound_member.get('name') or '').strip()
        candidates = fetch_all(
            "SELECT id,member_code,name FROM members WHERE status='ACTIVE' AND id=? AND member_code=? AND name=?",
            (bound_member.get('member_id'),bound_member.get('member_code'),name)) if name else []
        if len(candidates)==1:
            for index,row in enumerate(rows):
                if (result[index] is None and not row.get('platform_member_id') and not row.get('member_code')
                        and str(row.get('name') or '').strip()==name):
                    result[index]={'platform_member_id':str(candidates[0]['id']),
                        'member_code':str(candidates[0]['member_code']),
                        'identity_match':'BOUND_NAME_SELF_CONFIRMATION'}
    return result


def enrich_registration_payload(operation: str, payload: dict) -> dict:
    identity_fields = {"platform_member_id", "member_id", "member_code"}
    if operation == "registration":
        clean = {key: value for key, value in payload.items() if key not in identity_fields}
        return {**clean, **(registration_identities([clean])[0] or {})}
    clean = dict(payload)
    rows = [{key: value for key, value in row.items() if key not in identity_fields}
            for row in payload.get("attendees", [])]
    clean["attendees"] = [{**row, **(member or {})}
                          for row, member in zip(rows, registration_identities(rows))]
    return clean
