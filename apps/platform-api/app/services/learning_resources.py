"""Read-only learning navigation; never writes attendance or credit facts."""
from functools import lru_cache
import json
from pathlib import Path
from fastapi import HTTPException

COMMITTEE_ROLES = frozenset({"班主任", "副班主任", "班长", "辅导员", "组长", "运营班主任", "班委", "组委"})

@lru_cache(maxsize=1)
def _resources():
    return json.loads((Path(__file__).resolve().parents[1] / "data/learning_resources.json").read_text(encoding="utf-8"))

def resource_catalog():
    return {"years": [{"year": y["year"], "title": y["title"], "months": [
        {"number": m["number"], "label": m["label"], "qrCount": sum(len(m[k]) for k in ("classQrs", "groupQrs", "extraQrs"))}
        for m in y["months"]]} for y in _resources()["years"]]}

def month_resources(year, month):
    if year not in range(1, 4) or month not in range(1, 13):
        raise HTTPException(422, "学习年度或月份不正确")
    return _resources()["years"][year - 1]["months"][month - 1]

def committee_resources(services):
    if services.get("needs_manual_review") or not any(
        role.get("position_name") in COMMITTEE_ROLES for role in services.get("roles", [])
    ):
        raise HTTPException(403, "班组委运营资料仅向当前有效班组委岗位开放")
    data = _resources()
    return {"baidu": data["baidu"], "videos": data["committeeVideos"]}
