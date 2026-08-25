from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.learning_cycles import (
    bind_class_learning_plan,
    confirm_class_meeting,
    get_class_learning_progress,
    list_learning_plans,
    update_current_learning_cycle,
)


router = APIRouter(prefix="/api/v1", tags=["learning-plans"])


def _review_artifact_paths() -> tuple[Path, Path]:
    for parent in Path(__file__).resolve().parents:
        data_root = parent / "data" / "learning-plans"
        manifest = data_root / "standard-3y-2026.review.json"
        plan = data_root / "standard-3y-2026.json"
        if manifest.is_file() and plan.is_file():
            return manifest, plan
    raise FileNotFoundError("找不到2026学习计划审核清单")


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
        "task_count": len(tasks),
        "tasks": tasks,
    }


class LearningPlanBindingPayload(BaseModel):
    plan_version_id: int = Field(gt=0)
    cohort_month: int | None = Field(default=None, ge=1, le=12)
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
