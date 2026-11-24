"""换算表导入与分线启用领域服务。

围绕单位标定工单提供四件原应用里没有的能力：
1. 表格导入（CSV）并逐行预览，用原始序列试算「比例项 / 偏移项」两项对齐前的数值；
2. 换算规则的审批放行与分线启用，强制一台测点只能启用一套自洽的换算关系；
3. 对齐试读：试读请求在规则放行前接受、真正读数在放行后执行时，仍按请求时绑定的
   规则版本计算，同一次读取只采用一套换算，其余覆盖规则只作备选留痕；
4. 试读值与正式值核对，说明每行实际读到的数值、量纲与换算出处。

审核/启用完成前，在役规则与正式产出不受任何导入行为影响。
"""
from __future__ import annotations

import csv
import io
import time
from typing import Any

from app.services.base import BaseModuleService

# 换算表涉及的计量单位与其物理量纲
UNIT_DIMENSIONS: dict[str, str] = {
    "mV": "电压",
    "V": "电压",
    "MPa": "压力",
    "kPa": "压力",
    "bar": "压力",
}

# 每条产线（测点）最终要输出的工程量纲
LINE_ENGINEERING_DIMENSION: dict[str, str] = {
    "压缩空气线": "压力",
    "水压线": "压力",
}

LINE_POINTS: dict[str, str] = {
    "压缩空气线": "PT-AIR",
    "水压线": "PT-WATER",
}

RULE_TABLE = "calibration_rule"
IMPORT_TABLE = "calibration_import"
TRIAL_TABLE = "calibration_align_trial"

REQUIRED_COLUMNS = ["产线", "测点编号", "原始单位", "目标单位", "换算系数", "零点偏移", "规则类型"]


class CalibrationTableService(BaseModuleService):
    """换算表导入、试算预览、放行启用与对齐试读的领域服务。"""

    MODULE = "calibration_rule"
    ENTITY = "换算规则"

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _iso(ts: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))

    def _next_id(self, table: str) -> int:
        rows = self.store_rows(table)
        return max((int(r.get("id", 0)) for r in rows), default=0) + 1

    def store_rows(self, table: str) -> list[dict[str, Any]]:
        from app.store import store
        return store.rows(table)

    def _find_rule(self, rule_id: int) -> dict[str, Any] | None:
        for row in self.store_rows(RULE_TABLE):
            if int(row.get("id", 0)) == rule_id:
                return row
        return None

    def _active_rule(self, point: str) -> dict[str, Any] | None:
        """返回测点当前在役（已启用）的换算规则，任一时刻至多一条。"""
        for row in self.store_rows(RULE_TABLE):
            if row.get("测点编号") == point and row.get("启用状态") == "已启用":
                return row
        return None

    def _covering_rules(self, point: str) -> list[dict[str, Any]]:
        """覆盖该测点观测的全部规则（量纲冲突搁置的规则不具备覆盖资格）。"""
        return [
            row for row in self.store_rows(RULE_TABLE)
            if row.get("测点编号") == point and row.get("审批状态") != "冲突搁置"
        ]

    def _read_raw_samples(self, point: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._data_dir("edge_samples") / f"{point}.jsonl")

    @staticmethod
    def _raw_value(sample: dict[str, Any]) -> float:
        return float(sample.get("raw_value", sample.get("raw_value_mV", 0)))

    def _apply_rule(self, rule: dict[str, Any], raw: float) -> dict[str, Any]:
        """按单条规则计算，比例项与偏移项分开返回，体现对齐前的两项数值。"""
        scale = float(rule["换算系数"])
        offset = float(rule["零点偏移"])
        scale_term = round(raw * scale, 6)
        offset_term = round(offset, 6)
        return {
            "规则编号": rule["规则编号"],
            "规则类型": rule["规则类型"],
            "审批状态": rule["审批状态"],
            "启用状态": rule["启用状态"],
            "换算系数": scale,
            "零点偏移": offset,
            "比例项_对齐前": scale_term,
            "偏移项_对齐前": offset_term,
            "试算结果_对齐前": round(scale_term + offset_term, 6),
            "目标单位": rule["目标单位"],
            "目标量纲": UNIT_DIMENSIONS.get(rule["目标单位"], "未知"),
            "规则版本时间": rule["版本时间"],
            "来源导入批次": rule.get("来源导入批次", ""),
            "换算出处": f"{rule['规则编号']}（{rule['规则类型']}，批次 {rule.get('来源导入批次', '—')}，"
                        f"版本 {rule['版本时间']}）",
        }

    # ------------------------------------------------------------------
    # 1. 表格导入 + 逐行预览试算
    # ------------------------------------------------------------------

    def import_conversion_table(self, content: str, source_name: str = "换算表.csv") -> dict[str, Any]:
        """导入换算表 CSV：逐行校验量纲、落库规则、用原始序列试算对齐前比例/偏移项。

        导入只产生「待审核 / 冲突搁置」的规则，绝不改动在役规则与正式值。
        """
        parsed = self._parse_table(content)
        if isinstance(parsed, tuple):  # 表头级错误
            return {"ok": False, "message": parsed[1], "preview": [], "conflicts": []}

        import_id = self._next_id(IMPORT_TABLE)
        batch_no = f"IMPORT-{import_id:04d}"
        now = self._now()
        preview: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        accepted = 0

        for row in parsed:
            line = row["产线"].strip()
            point = row["测点编号"].strip()
            raw_unit = row["原始单位"].strip()
            target_unit = row["目标单位"].strip()
            rule_kind = row["规则类型"].strip() or "续订"
            try:
                scale = float(row["换算系数"])
                offset = float(row["零点偏移"])
            except (TypeError, ValueError):
                conflicts.append({
                    "行号": row["_行号"], "测点编号": point,
                    "原因": "换算系数或零点偏移不是数值，无法试算",
                    "目标单位": target_unit,
                })
                continue

            target_dim = UNIT_DIMENSIONS.get(target_unit)
            line_dim = LINE_ENGINEERING_DIMENSION.get(line)
            conflict_reason = ""
            if line_dim is None:
                conflict_reason = f"未知产线「{line}」，无法核对工程量纲"
            elif target_dim is None:
                conflict_reason = f"目标单位「{target_unit}」未登记量纲"
            elif target_dim != line_dim:
                conflict_reason = (
                    f"单位冲突：目标单位 {target_unit} 量纲为「{target_dim}」，"
                    f"与{line}工程输出量纲「{line_dim}」不一致"
                )

            rule_id = self._next_id(RULE_TABLE)
            rule_no = f"CONV-{point}-{rule_id:03d}"
            rule = {
                "id": rule_id,
                "规则编号": rule_no,
                "产线": line,
                "测点编号": point,
                "原始单位": raw_unit,
                "目标单位": target_unit,
                "换算系数": scale,
                "零点偏移": offset,
                "规则类型": rule_kind,
                "审批状态": "冲突搁置" if conflict_reason else "待审核",
                "启用状态": "未启用",
                "冲突原因": conflict_reason,
                "来源导入批次": batch_no,
                "版本时间": self._iso(now),
                "放行时间": None,
                "启用时间": None,
                "创建时间": self._iso(now),
                "status": "冲突搁置" if conflict_reason else "待审核",
                "pending": True,
                "abnormal": bool(conflict_reason),
            }
            self.store_rows(RULE_TABLE).append(rule)

            # 用该测点的原始序列逐点试算比例项与偏移项（对齐前口径）
            sample_trials = [
                {
                    "timestamp": s.get("timestamp", ""),
                    "原始值": self._raw_value(s),
                    "原始单位": raw_unit,
                    "原始量纲": UNIT_DIMENSIONS.get(raw_unit, "未知"),
                    **self._apply_rule(rule, self._raw_value(s)),
                }
                for s in self._read_raw_samples(point)
            ]
            row_preview = {
                "行号": row["_行号"],
                "规则编号": rule_no,
                "产线": line,
                "测点编号": point,
                "原始单位": raw_unit,
                "目标单位": target_unit,
                "目标量纲": target_dim or "未知",
                "换算系数": scale,
                "零点偏移": offset,
                "规则类型": rule_kind,
                "量纲校验": "冲突" if conflict_reason else "通过",
                "冲突原因": conflict_reason,
                "原始序列试算": sample_trials,
            }
            preview.append(row_preview)

            if conflict_reason:
                conflicts.append({
                    "行号": row["_行号"], "规则编号": rule_no, "测点编号": point,
                    "目标单位": target_unit, "目标量纲": target_dim or "未知",
                    "原因": conflict_reason,
                })
            else:
                accepted += 1

        batch = {
            "id": import_id,
            "批次编号": batch_no,
            "来源文件": source_name,
            "导入时间": self._iso(now),
            "导入时间戳": now,
            "总行数": len(parsed),
            "通过行数": accepted,
            "冲突行数": len(conflicts),
            "冲突明细": conflicts,
            "预览": preview,
            "status": "已冲突" if conflicts else "已导入",
            "pending": False,
            "abnormal": bool(conflicts),
        }
        self.store_rows(IMPORT_TABLE).append(batch)
        self._write_jsonl(
            self._data_dir("calibration_imports") / f"{batch_no}.jsonl",
            [{"batch": batch_no, "source": source_name, "at": now,
              "total": len(parsed), "accepted": accepted, "conflicts": conflicts,
              "preview": preview}],
        )

        # 含冲突行的导入：给受影响产线安排一轮对齐试读（先接受请求，读数稍后执行）
        scheduled = []
        conflict_points = sorted({c["测点编号"] for c in conflicts if c.get("测点编号")})
        for point in conflict_points:
            scheduled.append(self.schedule_align_trial(point, reason="导入表存在单位冲突，安排对齐复核轮"))

        return {
            "ok": True,
            "message": (
                f"批次 {batch_no} 导入完成：通过 {accepted} 行，冲突 {len(conflicts)} 行；"
                "通过行处于待审核，在役换算与正式值不受影响"
            ),
            "batch_id": import_id,
            "批次编号": batch_no,
            "preview": preview,
            "conflicts": conflicts,
            "scheduled_trials": scheduled,
        }

    @staticmethod
    def _parse_table(content: str) -> list[dict[str, Any]] | tuple[None, str]:
        text = (content or "").lstrip("﻿").strip()
        if not text:
            return None, "导入内容为空"
        reader = csv.DictReader(io.StringIO(text))
        headers = reader.fieldnames or []
        missing = [col for col in REQUIRED_COLUMNS if col not in headers]
        if missing:
            return None, f"换算表缺少必填列：{'、'.join(missing)}"
        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(reader, start=2):  # 含表头，数据从第 2 行起
            if not any(str(v or "").strip() for v in raw.values()):
                continue
            row = {key: (raw.get(key) or "").strip() for key in REQUIRED_COLUMNS}
            row["_行号"] = index
            rows.append(row)
        if not rows:
            return None, "换算表没有可导入的数据行"
        return rows

    def list_imports(self) -> list[dict[str, Any]]:
        return self.store_rows(IMPORT_TABLE)

    def get_import(self, import_id: int) -> dict[str, Any] | None:
        for row in self.store_rows(IMPORT_TABLE):
            if int(row.get("id", 0)) == import_id:
                return row
        return None

    # ------------------------------------------------------------------
    # 2. 审批放行与分线启用
    # ------------------------------------------------------------------

    def approve_rule(self, rule_id: int, approver: str = "审批人") -> dict[str, Any]:
        """审批人正式放行一条换算规则；冲突搁置行必须先修正并重新导入。"""
        rule = self._find_rule(rule_id)
        if rule is None:
            return {"ok": False, "message": f"换算规则 {rule_id} 不存在"}
        if rule["审批状态"] == "冲突搁置":
            return {"ok": False, "message": f"规则 {rule['规则编号']} 存在单位冲突（{rule['冲突原因']}），"
                                            "须修正压力单位并重新导入后才能放行"}
        if rule["审批状态"] == "已放行":
            return {"ok": True, "message": f"规则 {rule['规则编号']} 已放行，无需重复操作", "rule": rule}
        rule["审批状态"] = "已放行"
        rule["status"] = "已放行"
        rule["放行时间"] = self._iso(self._now())
        rule["放行时间戳"] = self._now()
        rule["审批人"] = approver
        rule["pending"] = False
        return {"ok": True, "message": f"规则 {rule['规则编号']} 已经{approver}正式放行", "rule": rule}

    def enable_eligibility(self, rule_id: int) -> dict[str, Any]:
        """给出规则在本次工单能否启用的判定与原因，不改变任何状态。"""
        rule = self._find_rule(rule_id)
        if rule is None:
            return {"eligible": False, "reasons": [f"换算规则 {rule_id} 不存在"], "notices": []}
        blockers: list[str] = []
        notices: list[str] = []
        if rule["审批状态"] == "冲突搁置":
            blockers.append(f"单位冲突未消除：{rule['冲突原因']}")
        if rule["审批状态"] == "待审核":
            blockers.append("续订尚未经审批人正式放行，审核前在役规则继续生效")
        active = self._active_rule(rule["测点编号"])
        if active is not None and active["id"] != rule["id"]:
            if rule["规则类型"] != "续订":
                blockers.append(
                    f"测点 {rule['测点编号']} 已有在役规则 {active['规则编号']}，"
                    "一台测点只能启用一套自洽换算，新规则须声明为续订替换"
                )
            else:
                notices.append(
                    f"启用后将停用在役规则 {active['规则编号']}（{active['目标单位']}），"
                    f"由 {rule['规则编号']}（{rule['目标单位']}）接续"
                )
        return {
            "eligible": rule["审批状态"] == "已放行" and not blockers,
            "reasons": blockers or (notices or ["可启用：规则已放行且无未决冲突"]),
            "notices": notices,
            "active_rule": active["规则编号"] if active else None,
        }

    def enable_rule(self, rule_id: int) -> dict[str, Any]:
        """分线启用一条已放行规则；同测点在役旧规同时停用，保证一套测点一套换算。"""
        rule = self._find_rule(rule_id)
        if rule is None:
            return {"ok": False, "message": f"换算规则 {rule_id} 不存在"}
        verdict = self.enable_eligibility(rule_id)
        if not verdict["eligible"]:
            return {"ok": False, "message": "规则暂不能启用：" + "；".join(verdict["reasons"]),
                    "eligibility": verdict}
        active = self._active_rule(rule["测点编号"])
        if active is not None and active["id"] != rule["id"]:
            active["启用状态"] = "已停用"
            active["status"] = "已停用"
            active["停用时间"] = self._iso(self._now())
        rule["启用状态"] = "已启用"
        rule["status"] = "已启用"
        rule["启用时间"] = self._iso(self._now())
        rule["pending"] = False
        audit = {
            "at": self._now(), "action": "enable_rule", "rule": rule["规则编号"],
            "point": rule["测点编号"], "replaced": active["规则编号"] if active else None,
        }
        self._write_jsonl(self._data_dir("calibration_audit") / "enablement.jsonl", [audit])
        return {
            "ok": True,
            "message": f"规则 {rule['规则编号']} 已在{rule['产线']}启用"
                       + (f"，旧规 {active['规则编号']} 已停用" if active else ""),
            "rule": rule,
            "replaced_rule": active["规则编号"] if active else None,
        }

    def list_rules(self, line: str | None = None, point: str | None = None) -> list[dict[str, Any]]:
        rows = self.store_rows(RULE_TABLE)
        if line:
            rows = [r for r in rows if r.get("产线") == line]
        if point:
            rows = [r for r in rows if r.get("测点编号") == point]
        return rows

    def line_summary(self) -> list[dict[str, Any]]:
        """每条产线（测点）一张工单视图：在役规则、待审/冲突规则与本次可启用清单。"""
        summary = []
        for line, point in LINE_POINTS.items():
            rules = self.list_rules(point=point)
            can_enable, blocked = [], []
            for rule in rules:
                verdict = self.enable_eligibility(rule["id"])
                item = {"规则编号": rule["规则编号"], "目标单位": rule["目标单位"],
                        "规则类型": rule["规则类型"], "审批状态": rule["审批状态"]}
                if rule["启用状态"] == "已启用":
                    item["判定"] = "在役（本次继续生效）"
                    can_enable.append(item)
                elif verdict["eligible"]:
                    item["判定"] = "本次工单可启用"
                    can_enable.append(item)
                else:
                    item["判定"] = "本次不可启用"
                    item["原因"] = "；".join(verdict["reasons"])
                    blocked.append(item)
            active = self._active_rule(point)
            summary.append({
                "产线": line,
                "测点编号": point,
                "在役规则": active["规则编号"] if active else None,
                "在役目标单位": active["目标单位"] if active else None,
                "规则总数": len(rules),
                "本次可启用": can_enable,
                "本次不可启用": blocked,
            })
        return summary

    # ------------------------------------------------------------------
    # 3. 对齐试读：请求时绑定规则版本，读数时只采用绑定的一套
    # ------------------------------------------------------------------

    def schedule_align_trial(self, point: str, reason: str = "对齐作业试读请求") -> dict[str, Any]:
        """接受对齐试读请求并绑定当时在役规则版本；此时不读取任何样本。"""
        active = self._active_rule(point)
        if active is None:
            return {"ok": False, "message": f"测点 {point} 没有在役换算规则，无法接受试读请求"}
        trial_id = self._next_id(TRIAL_TABLE)
        now = self._now()
        job = {
            "id": trial_id,
            "试读编号": f"TRIAL-{trial_id:04d}",
            "测点编号": point,
            "产线": active["产线"],
            "绑定规则编号": active["规则编号"],
            "绑定规则版本时间": active["版本时间"],
            "请求时间": self._iso(now),
            "请求时间戳": now,
            "请求原因": reason,
            "放行生效_读数时": None,
            "执行时间": None,
            "读数": [],
            "status": "已接受",
            "pending": True,
            "abnormal": False,
        }
        self.store_rows(TRIAL_TABLE).append(job)
        return {"ok": True, "message": f"试读请求 {job['试读编号']} 已接受，绑定 {active['规则编号']} 版本",
                "trial_id": trial_id, "试读编号": job["试读编号"], "绑定规则编号": active["规则编号"]}

    def run_align_trial(self, trial_id: int) -> dict[str, Any]:
        """真正读取原始样本：按请求时绑定的规则出数，其余覆盖规则只作未采用备选。"""
        job = next((r for r in self.store_rows(TRIAL_TABLE) if int(r.get("id", 0)) == trial_id), None)
        if job is None:
            return {"ok": False, "message": f"试读任务 {trial_id} 不存在"}
        bound = next((r for r in self.store_rows(RULE_TABLE)
                      if r.get("规则编号") == job["绑定规则编号"]), None)
        if bound is None:
            return {"ok": False, "message": f"绑定规则 {job['绑定规则编号']} 已不存在"}

        now = self._now()
        # 读数时刻是否已有更新的放行规则生效（不改变本次读数采用的规则）
        released_after_request = [
            r for r in self._covering_rules(job["测点编号"])
            if r["规则编号"] != bound["规则编号"]
            and r["审批状态"] == "已放行"
            and r.get("放行时间戳") is not None
            and r["放行时间戳"] > job["请求时间戳"]
        ]
        job["放行生效_读数时"] = bool(released_after_request)

        samples = self._read_raw_samples(job["测点编号"])
        readings = []
        for sample in samples:
            raw = self._raw_value(sample)
            applied = self._apply_rule(bound, raw)
            alternatives = []
            for rule in self._covering_rules(job["测点编号"]):
                if rule["规则编号"] == bound["规则编号"]:
                    continue
                alt = self._apply_rule(rule, raw)
                alt["是否采用"] = False
                alt["是否覆盖该观测"] = True
                alternatives.append(alt)
            readings.append({
                "timestamp": sample.get("timestamp", ""),
                "实际读到原始值": raw,
                "原始量纲": UNIT_DIMENSIONS.get(bound["原始单位"], "未知"),
                "原始单位": bound["原始单位"],
                "采用规则": bound["规则编号"],
                "采用换算": applied,
                "采用值": applied["试算结果_对齐前"],
                "采用量纲": applied["目标量纲"],
                "采用单位": applied["目标单位"],
                "换算出处": applied["换算出处"],
                "同次读取仅采用一套": True,
                "未采用备选": alternatives,
            })

        job["读数"] = readings
        job["执行时间"] = self._iso(now)
        job["执行时间戳"] = now
        job["status"] = "已执行"
        job["pending"] = False
        self._write_jsonl(
            self._data_dir("aligned_trials") / f"{job['试读编号']}.jsonl",
            [{"trial": job["试读编号"], "point": job["测点编号"],
              "bound_rule": bound["规则编号"], "bound_version": bound["版本时间"],
              "requested_at": job["请求时间"], "executed_at": job["执行时间"],
              "release_effective_at_read": job["放行生效_读数时"],
              "readings": readings}],
        )
        return {
            "ok": True,
            "message": (
                f"试读 {job['试读编号']} 完成：{len(readings)} 行均按绑定规则 {bound['规则编号']} 出数；"
                + ("读数时已有新放行规则生效，但本次读取仍采用请求时绑定的一套"
                   if released_after_request else "读数时无更新放行规则")
            ),
            "trial": job,
        }

    def list_trials(self, point: str | None = None) -> list[dict[str, Any]]:
        rows = self.store_rows(TRIAL_TABLE)
        if point:
            rows = [r for r in rows if r.get("测点编号") == point]
        return rows

    # ------------------------------------------------------------------
    # 4. 正式值与试读/正式差异核对
    # ------------------------------------------------------------------

    def formal_values(self, point: str) -> dict[str, Any]:
        """按当前在役规则计算正式值并落盘；审核前在役规则不变，正式值即不受影响。"""
        active = self._active_rule(point)
        if active is None:
            return {"ok": False, "message": f"测点 {point} 无在役换算规则，暂无正式值"}
        samples = self._read_raw_samples(point)
        records = []
        for sample in samples:
            raw = self._raw_value(sample)
            calc = self._apply_rule(active, raw)
            records.append({
                "point": point,
                "timestamp": sample.get("timestamp", ""),
                "raw_value": raw,
                "raw_unit": active["原始单位"],
                "formal_value": calc["试算结果_对齐前"],
                "formal_unit": active["目标单位"],
                "dimension": calc["目标量纲"],
                "source_rule": active["规则编号"],
                "rule_version": active["版本时间"],
                "换算出处": calc["换算出处"],
            })
        written = self._write_jsonl(
            self._data_dir("calibrated") / f"{point}_formal.jsonl", records
        )
        return {"ok": True, "point": point, "records": written,
                "source_rule": active["规则编号"], "items": records}

    def compare_trial_and_formal(self, trial_id: int) -> dict[str, Any]:
        """逐行核对同一次试读值与当前正式值的数值、量纲与换算出处差异。"""
        job = next((r for r in self.store_rows(TRIAL_TABLE) if int(r.get("id", 0)) == trial_id), None)
        if job is None:
            return {"ok": False, "message": f"试读任务 {trial_id} 不存在"}
        if job["status"] != "已执行":
            return {"ok": False, "message": f"试读 {job['试读编号']} 尚未执行读数，无法核对"}
        formal = self.formal_values(job["测点编号"])
        if not formal.get("ok"):
            return formal
        formal_by_ts = {item["timestamp"]: item for item in formal["items"]}
        diffs = []
        for reading in job["读数"]:
            f = formal_by_ts.get(reading["timestamp"])
            same_dim = f is not None and f["dimension"] == reading["采用量纲"]
            same_unit = f is not None and f["formal_unit"] == reading["采用单位"]
            delta = None
            if f is not None and same_dim and same_unit:
                delta = round(f["formal_value"] - reading["采用值"], 6)
            note = self._diff_note(reading, f, same_dim, same_unit)
            diffs.append({
                "timestamp": reading["timestamp"],
                "原始值": reading["实际读到原始值"],
                "原始量纲": reading["原始量纲"],
                "试读值": reading["采用值"],
                "试读单位": reading["采用单位"],
                "试读出处": reading["换算出处"],
                "正式值": f["formal_value"] if f else None,
                "正式单位": f["formal_unit"] if f else None,
                "正式出处": f["换算出处"] if f else None,
                "量纲一致": same_dim,
                "单位一致": same_unit,
                "差值_正式减试读": delta,
                "说明": note,
            })
        return {
            "ok": True,
            "trial": job["试读编号"],
            "point": job["测点编号"],
            "bound_rule": job["绑定规则编号"],
            "release_effective_at_read": job["放行生效_读数时"],
            "diffs": diffs,
        }

    def seed_demo_work_order(self) -> dict[str, Any]:
        """准备工单演示基线：两条产线的在役 MPa 旧规与含 300 mV 的原始样本。"""
        from app.store import store
        for table in (RULE_TABLE, IMPORT_TABLE, TRIAL_TABLE):
            store.rows(table).clear()
        # JSONL 工具为追加写入，重入时先清掉本工单的历史产物，避免样本重复
        for sub, pattern in [
            ("edge_samples", "PT-*.jsonl"),
            ("calibrated", "PT-*_formal.jsonl"),
            ("aligned_trials", "*.jsonl"),
            ("calibration_imports", "*.jsonl"),
            ("calibration_audit", "*.jsonl"),
        ]:
            for stale in self._data_dir(sub).glob(pattern):
                stale.unlink()
        samples_dir = self._data_dir("edge_samples")
        raw_samples = {
            "PT-AIR": [
                {"point": "PT-AIR", "timestamp": "2026-11-24T09:00:00", "raw_value": 300.0, "unit": "mV"},
                {"point": "PT-AIR", "timestamp": "2026-11-24T09:01:00", "raw_value": 150.0, "unit": "mV"},
            ],
            "PT-WATER": [
                {"point": "PT-WATER", "timestamp": "2026-11-24T09:00:00", "raw_value": 300.0, "unit": "mV"},
                {"point": "PT-WATER", "timestamp": "2026-11-24T09:02:00", "raw_value": 200.0, "unit": "mV"},
            ],
        }
        for point, samples in raw_samples.items():
            self._write_jsonl(samples_dir / f"{point}.jsonl", samples)
        baseline = {
            "PT-AIR": ("压缩空气线", 0.0005, 0.0),
            "PT-WATER": ("水压线", 0.0004, 0.02),
        }
        for point, (line, scale, offset) in baseline.items():
            self.store_rows(RULE_TABLE).append({
                "id": self._next_id(RULE_TABLE), "规则编号": f"CONV-{point}-OLD",
                "产线": line, "测点编号": point, "原始单位": "mV", "目标单位": "MPa",
                "换算系数": scale, "零点偏移": offset, "规则类型": "旧规",
                "审批状态": "已放行", "启用状态": "已启用", "冲突原因": "",
                "来源导入批次": "基线", "版本时间": "2026-11-01T00:00:00",
                "放行时间": "2026-11-01T00:00:00", "放行时间戳": 0.0,
                "启用时间": "2026-11-01T00:00:00", "创建时间": "2026-11-01T00:00:00",
                "status": "已启用", "pending": False, "abnormal": False,
            })
        return {"ok": True, "message": "演示基线已就绪：PT-AIR / PT-WATER 在役旧规（MPa）与 300 mV 原始样本"}

    @staticmethod
    def _diff_note(reading: dict[str, Any], formal: dict[str, Any] | None,
                   same_dim: bool, same_unit: bool) -> str:
        if formal is None:
            return "正式序列缺少对应观测"
        if formal["source_rule"] == reading["采用规则"]:
            return "试读与正式采用同一套换算，数值一致"
        if not same_dim:
            return (f"两套规则量纲不同（试读 {reading['采用单位']} / 正式 {formal['formal_unit']}），"
                    "同一观测只能采用一套，数值不可直接相减")
        if not same_unit:
            return (f"量纲同为压力但计量单位不同（试读 {reading['采用单位']} / 正式 {formal['formal_unit']}），"
                    "原始数值不可直接相减，须按 1 MPa = 1000 kPa 归一后核对")
        return (f"试读沿用请求时绑定的 {reading['采用规则']}，正式已切换为 "
                f"{formal['source_rule']}，同量纲同单位下存在数值差")
