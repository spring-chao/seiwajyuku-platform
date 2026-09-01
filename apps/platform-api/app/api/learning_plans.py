from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.learning_cycles import (
    bind_class_learning_plan,
    confirm_class_meeting,
    get_class_learning_progress,
    list_learning_plans,
    update_current_learning_cycle,
)
from app.services.course_credit_rules import (
    DEFAULT_PLAN_KEY,
    DEFAULT_VERSION_LABEL,
    create_course_credit_rule_version,
    get_group_meeting_credit_policy,
    list_course_credit_rules,
    update_course_credit_rule,
)


router = APIRouter(prefix="/api/v1", tags=["learning-plans"])


# 小组会基础出席分是周期级规则，不是每一条 GROUP_MEETING 流程任务的任务级学分。
# 规则由审核过的 course-credit-rules 工件提供，避免 API 与运行时各自维护分值。
GROUP_MEETING_CREDIT_POLICY = {
    **get_group_meeting_credit_policy(),
    "task_level_credit_editable": False,
}


def _review_artifact_paths() -> tuple[Path, Path]:
    for parent in Path(__file__).resolve().parents:
        data_root = parent / "data" / "learning-plans"
        manifest = data_root / "standard-3y-2026.review.json"
        plan = data_root / "standard-3y-2026.json"
        if manifest.is_file() and plan.is_file():
            return manifest, plan
    raise FileNotFoundError("找不到2026学习计划审核清单")


def _learning_plan_data_paths() -> dict[str, Path]:
    for parent in Path(__file__).resolve().parents:
        data_root = parent / "data" / "learning-plans"
        required = {
            "manifest": data_root / "standard-3y-2026.review.json",
            "plan": data_root / "standard-3y-2026.json",
            "flows": data_root / "group-meeting-flows-2026.1.json",
            "mapping": data_root / "cycle-flow-mapping-2026.1.json",
            "rules": data_root / "course-credit-rules-2026.json",
            "inventory": data_root / "group-meeting-source-inventory-2026.json",
            "c6_review": data_root / "standard-3y-2026.1.review.json",
        }
        if all(path.is_file() for path in required.values()):
            return required
    raise FileNotFoundError("找不到 L1.2-C 学习会流程配置工件")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _learning_plan_review_payload() -> dict:
    manifest_path, plan_path = _review_artifact_paths()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cycles_by_key = {
        (int(track["cohort_month"]), int(cycle["cycle_index"])): cycle
        for track in plan.get("cohort_tracks", [])
        for cycle in track.get("cycles", [])
    }
    checkpoints = []
    for checkpoint in manifest.get("checkpoints", []):
        key = (int(checkpoint["cohort_month"]), int(checkpoint["cycle_index"]))
        cycle = cycles_by_key.get(key)
        tasks = [] if cycle is None else [
            {
                "task_type": task.get("task_type"),
                "title": task.get("title"),
                "description": task.get("description"),
                "credit_points": task.get("credit_points"),
                "is_required": task.get("is_required"),
                "metadata": task.get("metadata"),
            }
            for task in cycle.get("tasks", [])
        ]
        checkpoints.append({**checkpoint, "tasks": tasks})
    confirmed_count = sum(item.get("status") == "CONFIRMED" for item in checkpoints)
    return {
        "review_schema_version": manifest.get("review_schema_version"),
        "plan_key": manifest.get("plan_key"),
        "version_label": manifest.get("version_label"),
        "status": manifest.get("status"),
        "required_checkpoint_count": manifest.get("required_checkpoint_count"),
        "confirmed_checkpoint_count": confirmed_count,
        "created_at": manifest.get("created_at"),
        "confirmed_at": manifest.get("confirmed_at"),
        "confirmed_by": manifest.get("confirmed_by"),
        "source_commit": manifest.get("source_commit"),
        "source_json": manifest.get("source_json"),
        "source_json_sha256": manifest.get("source_json_sha256"),
        "source_workbooks": manifest.get("source_workbooks", {}),
        "checkpoints": checkpoints,
    }


def _learning_plan_group_meeting_payload() -> dict:
    """Return every group-meeting task for the future adjustment workspace.

    The catalog is deliberately read-only.  The management page uses the
    returned fingerprints to keep any proposed changes local to the browser
    until a separately reviewed plan version is created.
    """

    manifest_path, plan_path = _review_artifact_paths()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks: list[dict] = []
    for track in plan.get("cohort_tracks", []):
        cohort_month = int(track["cohort_month"])
        for cycle in track.get("cycles", []):
            cycle_index = int(cycle["cycle_index"])
            for task_index, task in enumerate(cycle.get("tasks", []), start=1):
                if task.get("task_type") != "GROUP_MEETING":
                    continue
                metadata = task.get("metadata") or {}
                tasks.append(
                    {
                        "task_key": f"{cohort_month}-{cycle_index}-{task_index}",
                        "cohort_month": cohort_month,
                        "cycle_index": cycle_index,
                        "year_index": cycle.get("year_index"),
                        "year_cycle_index": cycle.get("year_cycle_index"),
                        "nominal_calendar_month": cycle.get("nominal_calendar_month"),
                        "task_type": "GROUP_MEETING",
                        "title": task.get("title"),
                        "description": task.get("description"),
                        "credit_points": task.get("credit_points"),
                        "is_required": bool(task.get("is_required")),
                        "sort_order": task.get("sort_order"),
                        "metadata": metadata,
                    }
                )
    return {
        "plan_key": manifest.get("plan_key"),
        "version_label": manifest.get("version_label"),
        "review_status": manifest.get("status"),
        "confirmed_at": manifest.get("confirmed_at"),
        "confirmed_by": manifest.get("confirmed_by"),
        "source_commit": manifest.get("source_commit"),
        "source_json": manifest.get("source_json"),
        "source_json_sha256": manifest.get("source_json_sha256"),
        "source_workbooks": manifest.get("source_workbooks", {}),
        "credit_policy": GROUP_MEETING_CREDIT_POLICY,
        "task_count": len(tasks),
        "tasks": tasks,
    }


def _learning_plan_group_meeting_flow_payload() -> dict:
    paths = _learning_plan_data_paths()
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    flows = json.loads(paths["flows"].read_text(encoding="utf-8"))
    mapping = json.loads(paths["mapping"].read_text(encoding="utf-8"))
    rules = json.loads(paths["rules"].read_text(encoding="utf-8"))
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    c6_review = json.loads(paths["c6_review"].read_text(encoding="utf-8"))
    source_files = [
        {
            "filename": item.get("filename"),
            "relative_path": item.get("relative_path"),
            "sha256": item.get("sha256"),
        }
        for item in inventory.get("included_files", [])
    ]
    return {
        "plan_key": manifest.get("plan_key"),
        "version_label": "2026.1",
        "base_version_label": manifest.get("version_label"),
        "status": flows.get("status", "DRAFT"),
        "base_review_status": manifest.get("status"),
        "confirmed_at": manifest.get("confirmed_at"),
        "confirmed_by": manifest.get("confirmed_by"),
        "source_commit": manifest.get("source_commit"),
        "source_json": manifest.get("source_json"),
        "source_json_sha256": manifest.get("source_json_sha256"),
        "source_workbooks": manifest.get("source_workbooks", {}),
        "base_group_flow_source_files": source_files,
        "base_course_credit_rules_sha256": _sha256_file(paths["rules"]),
        "source_inventory_sha256": flows.get("source_inventory_sha256"),
        "flow_count": len(flows.get("flows", [])),
        "source_fragment_count": sum(
            task.get("task_type") == "GROUP_MEETING"
            for track in plan.get("cohort_tracks", [])
            for cycle in track.get("cycles", [])
            for task in cycle.get("tasks", [])
        ),
        "quality_report": flows.get("quality_report", {}),
        "mapping_quality_report": mapping.get("quality_report", {}),
        "c6_review_status": c6_review.get("status", "PENDING"),
        "c6_summary": c6_review.get("summary", {}),
        "c6_review": c6_review,
        "credit_policy": GROUP_MEETING_CREDIT_POLICY,
        "course_credit_rules": rules,
        "flows": flows.get("flows", []),
        "mappings": mapping.get("mappings", []),
    }


class LearningPlanBindingPayload(BaseModel):
    plan_version_id: int = Field(gt=0)
    cohort_month: Literal[1, 4, 7, 10] | None = None
    started_at: str | None = Field(default=None, max_length=64)


class GroupLearningTaskUpdate(BaseModel):
    group_org_unit_id: str = Field(min_length=1, max_length=64)
    status: Literal["PENDING", "COMPLETED", "WAIVED"]
    note: str | None = Field(default=None, max_length=2000)


class LearningCycleUpdatePayload(BaseModel):
    planned_class_meeting_at: str | None = Field(default=None, max_length=64)
    class_meeting_status: Literal["PLANNED", "POSTPONED"] | None = None
    group_meeting_policy: Literal["REQUIRED", "SUSPENDED", "WAIVED"] | None = None
    adjustment_reason: str | None = Field(default=None, max_length=1000)
    group_tasks: list[GroupLearningTaskUpdate] = Field(default_factory=list, max_length=200)


class ConfirmClassMeetingPayload(BaseModel):
    actual_class_meeting_at: str | None = Field(default=None, max_length=64)
    source_event_group_id: int | None = Field(default=None, gt=0)
    confirmation_reason: str | None = Field(default=None, max_length=1000)


class CourseCreditRuleUpdatePayload(BaseModel):
    version_label: str = Field(default=DEFAULT_VERSION_LABEL, min_length=1, max_length=64)
    credit_points: int = Field(ge=0, le=999)
    status: Literal["PENDING", "CONFIGURED"] = "CONFIGURED"
    course_name: str | None = Field(default=None, min_length=1, max_length=255)
    year_index: int | None = Field(default=None, ge=1, le=20)
    aliases: list[str] | None = Field(default=None, max_length=30)


class CourseCreditRuleVersionPayload(BaseModel):
    plan_key: str = Field(default=DEFAULT_PLAN_KEY, min_length=1, max_length=128)
    version_label: str = Field(min_length=1, max_length=64)
    based_on_version_label: str = Field(default=DEFAULT_VERSION_LABEL, min_length=1, max_length=64)


@router.get("/learning-plans")
def learning_plans(user: dict = Depends(require_permission("plans:read"))) -> dict:
    return {"success": True, "data": list_learning_plans()}


@router.get("/learning-plan-review")
def learning_plan_review(user: dict = Depends(require_permission("plans:read"))) -> dict:
    try:
        return {"success": True, "data": _learning_plan_review_payload()}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "学习计划审核清单暂不可用") from exc


@router.get("/learning-plan-group-meetings")
def learning_plan_group_meetings(user: dict = Depends(require_permission("plans:read"))) -> dict:
    try:
        return {"success": True, "data": _learning_plan_group_meeting_payload()}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "小组学习会调整清单暂不可用") from exc


@router.get("/learning-plan-group-meeting-flows")
def learning_plan_group_meeting_flows(
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        return {"success": True, "data": _learning_plan_group_meeting_flow_payload()}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "小组学习会完整流程暂不可用") from exc


@router.get("/learning-plan-group-meeting-c6-review")
def learning_plan_group_meeting_c6_review(
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        payload = _learning_plan_group_meeting_flow_payload()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "小组学习会 C6 复核清单暂不可用") from exc
    return {
        "success": True,
        "data": {
            "plan_key": payload["plan_key"],
            "version_label": payload["version_label"],
            "status": payload["c6_review_status"],
            "summary": payload["c6_summary"],
            "review": payload["c6_review"],
        },
    }


@router.get("/learning-plan-group-meeting-flows/{flow_key}")
def learning_plan_group_meeting_flow(
    flow_key: str,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        payload = _learning_plan_group_meeting_flow_payload()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "小组学习会完整流程暂不可用") from exc
    flow = next((item for item in payload["flows"] if item.get("flow_key") == flow_key), None)
    if flow is None:
        raise HTTPException(404, "未找到小组学习会流程")
    return {
        "success": True,
        "data": {
            "plan_key": payload["plan_key"],
            "version_label": payload["version_label"],
            "source_commit": payload["source_commit"],
            "source_json_sha256": payload["source_json_sha256"],
            "base_group_flow_source_files": payload["base_group_flow_source_files"],
            "base_course_credit_rules_sha256": payload["base_course_credit_rules_sha256"],
            "flow": flow,
        },
    }


@router.get("/learning-plan-course-credit-rules")
def learning_plan_course_credit_rules(
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        payload = _learning_plan_group_meeting_flow_payload()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "课程积分规则暂不可用") from exc
    return {
        "success": True,
        "data": {
            "plan_key": payload["plan_key"],
            "version_label": payload["version_label"],
            "sha256": payload["base_course_credit_rules_sha256"],
            "rules": payload["course_credit_rules"],
        },
    }


@router.get("/learning-plan-course-credit-config")
def learning_plan_course_credit_config(
    plan_key: str = Query(default=DEFAULT_PLAN_KEY, min_length=1, max_length=128),
    version_label: str = Query(default=DEFAULT_VERSION_LABEL, min_length=1, max_length=64),
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        data = list_course_credit_rules(plan_key, version_label)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(503, "课程积分配置目录暂不可用") from exc
    data["can_manage"] = "plans:credit_rules_manage" in user["permissions"]
    return {"success": True, "data": data}


@router.put("/learning-plan-course-credit-config/{course_key}")
def update_learning_plan_course_credit_config(
    course_key: str,
    payload: CourseCreditRuleUpdatePayload,
    user: dict = Depends(require_permission("plans:credit_rules_manage")),
) -> dict:
    try:
        data = update_course_credit_rule(
            actor_user_id=user["id"],
            plan_key=DEFAULT_PLAN_KEY,
            version_label=payload.version_label,
            course_key=course_key,
            credit_points=payload.credit_points,
            status=payload.status,
            course_name=payload.course_name,
            year_index=payload.year_index,
            aliases=payload.aliases,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/learning-plan-course-credit-config/versions")
def create_learning_plan_course_credit_config_version(
    payload: CourseCreditRuleVersionPayload,
    user: dict = Depends(require_permission("plans:credit_rules_manage")),
) -> dict:
    try:
        data = create_course_credit_rule_version(
            actor_user_id=user["id"],
            plan_key=payload.plan_key,
            version_label=payload.version_label,
            based_on_version_label=payload.based_on_version_label,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/classes/{class_org_unit_id}/learning-plan-binding")
def create_learning_plan_binding(
    class_org_unit_id: str,
    payload: LearningPlanBindingPayload,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = bind_class_learning_plan(
            actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
            plan_version_id=payload.plan_version_id, cohort_month=payload.cohort_month,
            started_at=payload.started_at,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/classes/{class_org_unit_id}/learning-progress")
def learning_progress(
    class_org_unit_id: str,
    at: str | None = None,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        data = get_class_learning_progress(
            user_id=user["id"], class_org_unit_id=class_org_unit_id, at=at
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"success": True, "data": data}


@router.patch("/classes/{class_org_unit_id}/learning-cycles/current")
def update_learning_cycle(
    class_org_unit_id: str,
    payload: LearningCycleUpdatePayload,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = update_current_learning_cycle(
            actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
            updates=payload.model_dump(exclude_unset=True),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/classes/{class_org_unit_id}/learning-cycles/current/confirm-class-meeting")
def confirm_learning_cycle_class_meeting(
    class_org_unit_id: str,
    payload: ConfirmClassMeetingPayload,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = confirm_class_meeting(
            actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
            actual_class_meeting_at=payload.actual_class_meeting_at,
            source_event_group_id=payload.source_event_group_id,
            confirmation_reason=payload.confirmation_reason,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
