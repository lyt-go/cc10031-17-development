"""单位标定工单专用路由：换算表导入预览、审批放行、分线启用、对齐试读与差异核对。

路由层只做参数接收与转发，所有业务判断在 CalibrationTableService 内完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.calibration_table import CalibrationTableService

router = APIRouter(prefix="/api/unit-calibration", tags=["单位标定工单"])
service = CalibrationTableService()


class ImportTablePayload(BaseModel):
    content: str
    source_name: str = "换算表.csv"


class ApprovePayload(BaseModel):
    approver: str = "审批人"


class TrialSchedulePayload(BaseModel):
    point: str
    reason: str = "对齐作业试读请求"


@router.post("/demo-seed", response_model=dict)
def seed_demo_work_order() -> dict[str, Any]:
    return service.seed_demo_work_order()


@router.post("/conversion-table/import", response_model=dict)
def import_conversion_table(payload: ImportTablePayload) -> dict[str, Any]:
    return service.import_conversion_table(payload.content, payload.source_name)


@router.get("/conversion-table/imports", response_model=list)
def list_imports() -> list[dict[str, Any]]:
    return service.list_imports()


@router.get("/conversion-table/imports/{import_id}", response_model=dict)
def get_import(import_id: int) -> dict[str, Any]:
    batch = service.get_import(import_id)
    if batch is None:
        return {"ok": False, "message": f"导入批次 {import_id} 不存在"}
    return batch


@router.get("/rules", response_model=list)
def list_rules(line: str | None = None, point: str | None = None) -> list[dict[str, Any]]:
    return service.list_rules(line=line, point=point)


@router.post("/rules/{rule_id}/approve", response_model=dict)
def approve_rule(rule_id: int, payload: ApprovePayload) -> dict[str, Any]:
    return service.approve_rule(rule_id, payload.approver)


@router.get("/rules/{rule_id}/eligibility", response_model=dict)
def rule_eligibility(rule_id: int) -> dict[str, Any]:
    return service.enable_eligibility(rule_id)


@router.post("/rules/{rule_id}/enable", response_model=dict)
def enable_rule(rule_id: int) -> dict[str, Any]:
    return service.enable_rule(rule_id)


@router.get("/lines/summary", response_model=list)
def line_summary() -> list[dict[str, Any]]:
    return service.line_summary()


@router.post("/align-trials", response_model=dict)
def schedule_trial(payload: TrialSchedulePayload) -> dict[str, Any]:
    return service.schedule_align_trial(payload.point, payload.reason)


@router.post("/align-trials/{trial_id}/run", response_model=dict)
def run_trial(trial_id: int) -> dict[str, Any]:
    return service.run_align_trial(trial_id)


@router.get("/align-trials", response_model=list)
def list_trials(point: str | None = None) -> list[dict[str, Any]]:
    return service.list_trials(point)


@router.get("/formal-values/{point}", response_model=dict)
def formal_values(point: str) -> dict[str, Any]:
    return service.formal_values(point)


@router.get("/align-trials/{trial_id}/comparison", response_model=dict)
def compare_trial_and_formal(trial_id: int) -> dict[str, Any]:
    return service.compare_trial_and_formal(trial_id)
