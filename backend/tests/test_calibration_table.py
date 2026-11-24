"""单位标定工单验收测试：换算表导入、试算预览、放行、分线启用与对齐试读全链路。

叙事时间线：
1. 压缩空气线 PT-AIR、水压线 PT-WATER 各有在役旧规；原始样本含 300 mV 观测。
2. 导入含一行单位冲突的换算表（水压线目标单位误填 V）→ 冲突行搁置，并给水压线
   安排一轮对齐试读；其余行进入待审核，在役规则与正式值不变。
3. 水压线对齐试读请求在修订放行之前被接受（绑定旧规版本）。
4. 压缩空气线 300 mV 同时匹配 kPa 续订与 MPa 旧规，只能启用一套；kPa 续订未放行，
   本工单不可启用，MPa 旧规继续在役。
5. 水压线修订经审批人正式放行；真正执行试读时放行已生效，但同一次读取仍只采用
   请求时绑定的旧规一套，放行后的新规仅作未采用备选。
6. 修正压力单位重新导入 → 新行通过、启用并替换旧规；核对两条线试读与正式值差异，
   以及本次工单各规则的可启用范围。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.calibration_table import (
    CalibrationTableService,
    LINE_POINTS,
    RULE_TABLE,
    TRIAL_TABLE,
)

AIR = LINE_POINTS["压缩空气线"]   # PT-AIR
WATER = LINE_POINTS["水压线"]     # PT-WATER


def _csv(rows: list[dict[str, str]]) -> str:
    header = "产线,测点编号,原始单位,目标单位,换算系数,零点偏移,规则类型"
    lines = [header]
    for row in rows:
        lines.append(",".join(row[col] for col in
                               ["产线", "测点编号", "原始单位", "目标单位",
                                "换算系数", "零点偏移", "规则类型"]))
    return "\n".join(lines) + "\n"


@pytest.fixture()
def svc_with_data(tmp_path: Path):
    """准备原始样本（含 300 mV 观测）和两条在役旧规，数据目录指向临时路径。"""
    from app.store import store
    # 全局内存仓库在测试间复用，先清空本工单涉及的三张表，避免状态串扰
    for table in (RULE_TABLE, "calibration_import", TRIAL_TABLE):
        store.rows(table).clear()
    with patch("app.services.base.DATA_ROOT", tmp_path):
        import app.services.calibration_table as ct
        ct.DATA_ROOT = tmp_path
        svc = CalibrationTableService()

        samples_dir = tmp_path / "edge_samples"
        samples_dir.mkdir(parents=True, exist_ok=True)
        # 两条线都包含原值 300 mV 的观测
        air_samples = [
            {"point": AIR, "timestamp": "2026-11-24T09:00:00", "raw_value": 300.0, "unit": "mV"},
            {"point": AIR, "timestamp": "2026-11-24T09:01:00", "raw_value": 150.0, "unit": "mV"},
        ]
        water_samples = [
            {"point": WATER, "timestamp": "2026-11-24T09:00:00", "raw_value": 300.0, "unit": "mV"},
            {"point": WATER, "timestamp": "2026-11-24T09:02:00", "raw_value": 200.0, "unit": "mV"},
        ]
        for point, samples in [(AIR, air_samples), (WATER, water_samples)]:
            with open(samples_dir / f"{point}.jsonl", "w", encoding="utf-8") as f:
                for s in samples:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")

        # 在役旧规：两条线都继续输出 MPa
        baseline = {
            AIR: ("压缩空气线", "0.0005", "0"),       # 300 mV -> 0.15 MPa
            WATER: ("水压线", "0.0004", "0.02"),      # 300 mV -> 0.14 MPa
        }
        for point, (line, scale, offset) in baseline.items():
            svc.store_rows(RULE_TABLE).append({
                "id": svc._next_id(RULE_TABLE), "规则编号": f"CONV-{point}-OLD",
                "产线": line, "测点编号": point, "原始单位": "mV", "目标单位": "MPa",
                "换算系数": float(scale), "零点偏移": float(offset), "规则类型": "旧规",
                "审批状态": "已放行", "启用状态": "已启用", "冲突原因": "",
                "来源导入批次": "基线", "版本时间": "2026-11-01T00:00:00",
                "放行时间": "2026-11-01T00:00:00", "启用时间": "2026-11-01T00:00:00",
                "创建时间": "2026-11-01T00:00:00", "status": "已启用",
                "pending": False, "abnormal": False,
            })
        yield svc, tmp_path


class TestConversionTableImport:
    def test_import_with_unit_conflict_shelves_row_and_schedules_trial(self, svc_with_data):
        svc, tmp_path = svc_with_data
        table = _csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "V",
             "换算系数": "0.001", "零点偏移": "0", "规则类型": "续订"},
        ])
        result = svc.import_conversion_table(table, source_name="换算表_第一轮.csv")

        assert result["ok"] is True
        assert result["conflicts"][0]["测点编号"] == WATER
        assert "单位冲突" in result["conflicts"][0]["原因"]
        # 冲突行搁置，空气线行待审核
        rules = svc.list_rules()
        air_new = next(r for r in rules if r["测点编号"] == AIR and r["目标单位"] == "kPa")
        water_bad = next(r for r in rules if r["测点编号"] == WATER and r["目标单位"] == "V")
        assert air_new["审批状态"] == "待审核"
        assert water_bad["审批状态"] == "冲突搁置"
        # 导入自动给水压线安排一轮对齐试读
        assert result["scheduled_trials"]
        assert result["scheduled_trials"][0]["ok"] is True
        trials = svc.list_trials(point=WATER)
        assert len(trials) == 1 and trials[0]["status"] == "已接受"
        # 导入批次与预览落盘
        assert (tmp_path / "calibration_imports").exists()

    def test_preview_uses_raw_series_and_splits_scale_offset_terms(self, svc_with_data):
        svc, _ = svc_with_data
        table = _csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "1.5", "规则类型": "续订"},
        ])
        result = svc.import_conversion_table(table)
        preview = result["preview"][0]
        assert len(preview["原始序列试算"]) == 2
        row_300 = next(t for t in preview["原始序列试算"] if t["原始值"] == 300.0)
        # 比例项 300*0.5=150，偏移项 1.5，对齐前结果 151.5 kPa
        assert row_300["比例项_对齐前"] == 150.0
        assert row_300["偏移项_对齐前"] == 1.5
        assert row_300["试算结果_对齐前"] == 151.5
        assert row_300["原始单位"] == "mV" and row_300["原始量纲"] == "电压"
        assert row_300["目标单位"] == "kPa" and row_300["目标量纲"] == "压力"

    def test_missing_column_is_rejected(self, svc_with_data):
        svc, _ = svc_with_data
        result = svc.import_conversion_table("产线,测点编号\n压缩空气线,PT-AIR\n")
        assert result["ok"] is False
        assert "缺少必填列" in result["message"]

    def test_import_does_not_change_active_rule_or_formal_values(self, svc_with_data):
        svc, _ = svc_with_data
        before = svc.formal_values(AIR)
        table = _csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
        ])
        svc.import_conversion_table(table)
        after = svc.formal_values(AIR)
        assert [i["formal_value"] for i in before["items"]] == \
               [i["formal_value"] for i in after["items"]]
        assert svc._active_rule(AIR)["目标单位"] == "MPa"


class TestAirLineTwoRulesOneRawValue:
    def test_300mv_matches_both_kpa_renewal_and_mpa_legacy(self, svc_with_data):
        svc, _ = svc_with_data
        table = _csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
        ])
        result = svc.import_conversion_table(table)
        kpa_rule = next(r for r in svc.list_rules(point=AIR) if r["目标单位"] == "kPa")
        mpa_rule = svc._active_rule(AIR)
        # 两条规则都覆盖 300 mV，数值结果分别为 150 kPa 与 0.15 MPa
        calc_kpa = svc._apply_rule(kpa_rule, 300.0)
        calc_mpa = svc._apply_rule(mpa_rule, 300.0)
        assert calc_kpa["试算结果_对齐前"] == 150.0 and calc_kpa["目标单位"] == "kPa"
        assert calc_mpa["试算结果_对齐前"] == 0.15 and calc_mpa["目标单位"] == "MPa"
        assert result["preview"][0]["原始序列试算"][0]["原始值"] == 300.0

    def test_unapproved_kpa_renewal_cannot_enable_mpa_legacy_stays_active(self, svc_with_data):
        svc, _ = svc_with_data
        svc.import_conversion_table(_csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
        ]))
        kpa_rule = next(r for r in svc.list_rules(point=AIR) if r["目标单位"] == "kPa")
        verdict = svc.enable_eligibility(kpa_rule["id"])
        assert verdict["eligible"] is False
        assert any("尚未经审批人正式放行" in r for r in verdict["reasons"])
        enable = svc.enable_rule(kpa_rule["id"])
        assert enable["ok"] is False
        # 旧规继续在役，仍是 MPa
        assert svc._active_rule(AIR)["目标单位"] == "MPa"

    def test_approve_then_enable_kpa_replaces_mpa_keeping_single_active_rule(self, svc_with_data):
        svc, _ = svc_with_data
        svc.import_conversion_table(_csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
        ]))
        kpa_rule = next(r for r in svc.list_rules(point=AIR) if r["目标单位"] == "kPa")
        mpa_rule = svc._active_rule(AIR)
        svc.approve_rule(kpa_rule["id"], approver="审批人张工")
        assert kpa_rule["审批状态"] == "已放行" and kpa_rule["放行时间"]
        enabled = svc.enable_rule(kpa_rule["id"])
        assert enabled["ok"] is True and enabled["replaced_rule"] == mpa_rule["规则编号"]
        actives = [r for r in svc.list_rules(point=AIR) if r["启用状态"] == "已启用"]
        assert len(actives) == 1 and actives[0]["目标单位"] == "kPa"
        assert mpa_rule["启用状态"] == "已停用"


class TestWaterLineReleaseAndBoundTrial:
    def test_trial_accepted_before_release_uses_bound_rule_after_release(self, svc_with_data):
        svc, tmp_path = svc_with_data
        # 第一轮导入：水压线单位冲突（V），自动安排的试读即冲突复核轮
        first = svc.import_conversion_table(_csv([
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "V",
             "换算系数": "0.001", "零点偏移": "0", "规则类型": "续订"},
        ]))
        assert first["scheduled_trials"]
        trial_id = first["scheduled_trials"][0]["trial_id"]
        job = next(r for r in svc.store_rows(TRIAL_TABLE) if r["id"] == trial_id)
        bound_rule_no = job["绑定规则编号"]
        assert bound_rule_no == "CONV-PT-WATER-OLD"

        # 修正压力单位重新导入，新规则经审批人正式放行
        second = svc.import_conversion_table(_csv([
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "MPa",
             "换算系数": "0.0006", "零点偏移": "0.01", "规则类型": "续订"},
        ]))
        assert second["conflicts"] == []
        new_rule = next(r for r in svc.list_rules(point=WATER)
                        if r["来源导入批次"] == second["批次编号"])
        approval = svc.approve_rule(new_rule["id"], approver="审批人李工")
        assert approval["ok"] is True
        # 此时尚未启用，正式值仍来自旧规（审核前在役值不受影响）
        assert svc._active_rule(WATER)["规则编号"] == bound_rule_no

        # 真正读取水压样本：放行已生效，但本次仍按请求时绑定的旧规出数
        run = svc.run_align_trial(trial_id)
        assert run["ok"] is True
        assert run["trial"]["放行生效_读数时"] is True
        readings = run["trial"]["读数"]
        assert len(readings) == 2
        r300 = next(x for x in readings if x["实际读到原始值"] == 300.0)
        # 每行必须交代数值、量纲、单位与换算出处
        assert r300["采用规则"] == bound_rule_no
        assert r300["实际读到原始值"] == 300.0
        assert r300["原始量纲"] == "电压" and r300["原始单位"] == "mV"
        assert r300["采用值"] == 0.14 and r300["采用单位"] == "MPa"  # 300*0.0004+0.02
        assert r300["采用量纲"] == "压力"
        assert bound_rule_no in r300["换算出处"]
        assert r300["同次读取仅采用一套"] is True
        # 放行后的新公式同样覆盖该观测，但只作未采用备选
        alts = r300["未采用备选"]
        alt_new = next(a for a in alts if a["规则编号"] == new_rule["规则编号"])
        assert alt_new["是否采用"] is False and alt_new["是否覆盖该观测"] is True
        assert alt_new["试算结果_对齐前"] == 0.19  # 300*0.0006+0.01
        # 试读落盘
        assert (tmp_path / "aligned_trials" / f"{job['试读编号']}.jsonl").exists()

    def test_enable_released_water_rule_switches_formal_values(self, svc_with_data):
        svc, _ = svc_with_data
        svc.import_conversion_table(_csv([
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "V",
             "换算系数": "0.001", "零点偏移": "0", "规则类型": "续订"},
        ]))
        second = svc.import_conversion_table(_csv([
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "MPa",
             "换算系数": "0.0006", "零点偏移": "0.01", "规则类型": "续订"},
        ]))
        new_rule = next(r for r in svc.list_rules(point=WATER)
                        if r["来源导入批次"] == second["批次编号"])
        svc.approve_rule(new_rule["id"])
        enabled = svc.enable_rule(new_rule["id"])
        assert enabled["ok"] is True
        formal = svc.formal_values(WATER)
        f300 = next(i for i in formal["items"] if i["raw_value"] == 300.0)
        assert f300["formal_value"] == 0.19 and f300["formal_unit"] == "MPa"
        assert new_rule["规则编号"] in f300["换算出处"]
        # 同一测点仍只有一套在役换算
        assert len([r for r in svc.list_rules(point=WATER) if r["启用状态"] == "已启用"]) == 1

    def test_conflicted_rule_cannot_be_approved(self, svc_with_data):
        svc, _ = svc_with_data
        svc.import_conversion_table(_csv([
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "V",
             "换算系数": "0.001", "零点偏移": "0", "规则类型": "续订"},
        ]))
        bad = next(r for r in svc.list_rules(point=WATER) if r["目标单位"] == "V")
        result = svc.approve_rule(bad["id"])
        assert result["ok"] is False
        assert "修正压力单位并重新导入" in result["message"]


class TestTrialFormalComparison:
    def test_water_comparison_shows_rule_switch_delta(self, svc_with_data):
        svc, _ = svc_with_data
        scheduled = svc.schedule_align_trial(WATER, reason="放行前试读")
        trial_id = scheduled["trial_id"]
        second = svc.import_conversion_table(_csv([
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "MPa",
             "换算系数": "0.0006", "零点偏移": "0.01", "规则类型": "续订"},
        ]))
        new_rule = next(r for r in svc.list_rules(point=WATER)
                        if r["来源导入批次"] == second["批次编号"])
        svc.approve_rule(new_rule["id"])
        svc.run_align_trial(trial_id)
        svc.enable_rule(new_rule["id"])

        comparison = svc.compare_trial_and_formal(trial_id)
        assert comparison["ok"] is True
        row = next(d for d in comparison["diffs"] if d["原始值"] == 300.0)
        assert row["试读值"] == 0.14 and row["正式值"] == 0.19
        assert row["量纲一致"] is True and row["单位一致"] is True
        assert row["差值_正式减试读"] == pytest.approx(0.05)
        assert "已切换" in row["说明"]

    def test_air_comparison_keeps_single_unit_when_kpa_blocked(self, svc_with_data):
        svc, _ = svc_with_data
        scheduled = svc.schedule_align_trial(AIR, reason="空气线试读")
        svc.import_conversion_table(_csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
        ]))
        # kPa 续订不放行不启用；试读与正式都走 MPa 旧规
        svc.run_align_trial(scheduled["trial_id"])
        comparison = svc.compare_trial_and_formal(scheduled["trial_id"])
        row = next(d for d in comparison["diffs"] if d["原始值"] == 300.0)
        assert row["试读值"] == row["正式值"] == 0.15
        assert row["试读单位"] == row["正式单位"] == "MPa"
        assert row["差值_正式减试读"] == 0
        assert "同一套换算" in row["说明"]

    def test_unit_difference_blocks_numeric_subtraction(self, svc_with_data):
        svc, _ = svc_with_data
        # 试读绑定 MPa 旧规后，启用 kPa 续订，核对时量纲同、单位不同
        scheduled = svc.schedule_align_trial(AIR, reason="单位切换试读")
        svc.import_conversion_table(_csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
        ]))
        kpa = next(r for r in svc.list_rules(point=AIR) if r["目标单位"] == "kPa")
        svc.approve_rule(kpa["id"])
        svc.run_align_trial(scheduled["trial_id"])
        svc.enable_rule(kpa["id"])
        comparison = svc.compare_trial_and_formal(scheduled["trial_id"])
        row = next(d for d in comparison["diffs"] if d["原始值"] == 300.0)
        assert row["量纲一致"] is True and row["单位一致"] is False
        assert row["差值_正式减试读"] is None
        assert "1 MPa = 1000 kPa" in row["说明"]


class TestDemoSeedReentry:
    def test_seed_twice_does_not_duplicate_samples(self, svc_with_data):
        svc, tmp_path = svc_with_data
        svc.seed_demo_work_order()
        svc.seed_demo_work_order()
        samples = svc._read_raw_samples(AIR)
        assert len(samples) == 2
        assert len(svc.list_rules(point=AIR)) == 1
        assert svc.list_imports() == [] and svc.list_trials() == []


class TestLineSummaryAndWorkOrderScope:
    def test_line_summary_states_what_can_enable_in_this_work_order(self, svc_with_data):
        svc, _ = svc_with_data
        svc.import_conversion_table(_csv([
            {"产线": "压缩空气线", "测点编号": AIR, "原始单位": "mV", "目标单位": "kPa",
             "换算系数": "0.5", "零点偏移": "0", "规则类型": "续订"},
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "V",
             "换算系数": "0.001", "零点偏移": "0", "规则类型": "续订"},
        ]))
        summary = svc.line_summary()
        air = next(s for s in summary if s["产线"] == "压缩空气线")
        water = next(s for s in summary if s["产线"] == "水压线")
        # 两条线在役规则均为旧规 MPa
        assert air["在役目标单位"] == "MPa" and water["在役目标单位"] == "MPa"
        # kPa 续订未放行、V 行冲突搁置：本工单都不可启用
        blocked_targets = {item["目标单位"] for item in air["本次不可启用"] + water["本次不可启用"]}
        assert {"kPa", "V"} <= blocked_targets
        # 放行并修正后再看汇总
        kpa = next(r for r in svc.list_rules(point=AIR) if r["目标单位"] == "kPa")
        svc.approve_rule(kpa["id"])
        svc.enable_rule(kpa["id"])
        re_import = svc.import_conversion_table(_csv([
            {"产线": "水压线", "测点编号": WATER, "原始单位": "mV", "目标单位": "MPa",
             "换算系数": "0.0006", "零点偏移": "0.01", "规则类型": "续订"},
        ]))
        new_water = next(r for r in svc.list_rules(point=WATER)
                         if r["来源导入批次"] == re_import["批次编号"])
        svc.approve_rule(new_water["id"])
        summary2 = svc.line_summary()
        air2 = next(s for s in summary2 if s["产线"] == "压缩空气线")
        water2 = next(s for s in summary2 if s["产线"] == "水压线")
        assert air2["在役目标单位"] == "kPa"
        assert any(item["规则编号"] == new_water["规则编号"]
                   and item["判定"] == "本次工单可启用" for item in water2["本次可启用"])
