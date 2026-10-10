"""Associate registration slots only with a unique active name/phone identity."""
from __future__ import annotations

from app.core.privacy import phone_hash
from app.db import fetch_all


def registration_identities(rows: list[dict]) -> list[dict | None]:
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
