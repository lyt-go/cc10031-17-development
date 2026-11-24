<template>
  <section class="page calibration-page" data-testid="calibration-workbench">
    <header class="page-head">
      <div>
        <h2>单位标定工单</h2>
        <p class="page-desc">
          换算表导入预览、比例/偏移两项对齐前试算、审批放行、分线启用与对齐试读核对。
          审核启用前在役换算与正式值不受影响，一台测点只启用一套自洽换算。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="demo-seed" @click="seedDemo">
          载入演示基线
        </button>
      </div>
    </header>

    <p v-if="message" class="page-foot" :class="{ 'error-text': hasError }">{{ message }}</p>

    <!-- 1. 换算表导入与逐行预览 -->
    <article class="cal-panel">
      <h3>① 换算表导入（逐行预览，比例项/偏移项分开试算）</h3>
      <form class="analysis-form" @submit.prevent="onImport">
        <label>
          来源文件名
          <input v-model="sourceName" data-testid="import-source-name" required />
        </label>
        <label class="csv-input">
          CSV 内容（产线,测点编号,原始单位,目标单位,换算系数,零点偏移,规则类型）
          <textarea v-model="csvContent" data-testid="import-csv" rows="5" :placeholder="csvPlaceholder"></textarea>
        </label>
        <button class="btn primary" data-testid="submit-import" type="submit" :disabled="busy">
          {{ busy ? '导入中…' : '导入并试算预览' }}
        </button>
      </form>

      <div v-for="batch in imports" :key="batch.id" class="import-block" data-testid="import-batch">
        <header>
          <strong>{{ batch.批次编号 }}</strong>
          <span>{{ batch.来源文件 }} · {{ batch.导入时间 }}</span>
          <span>共 {{ batch.总行数 }} 行 / 通过 {{ batch.通过行数 }} / 冲突 {{ batch.冲突行数 }}</span>
          <span :class="batch.冲突行数 ? 'error-text' : ''">{{ batch.status }}</span>
        </header>
        <table class="data-table">
          <thead>
            <tr>
              <th>规则编号</th><th>产线/测点</th><th>原始单位</th><th>目标单位(量纲)</th>
              <th>换算系数</th><th>零点偏移</th><th>量纲校验</th><th>原始序列试算（对齐前比例项+偏移项）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in batch.预览" :key="row.规则编号" :data-conflict="row.量纲校验">
              <td>{{ row.规则编号 }}</td>
              <td>{{ row.产线 }} / {{ row.测点编号 }}</td>
              <td>{{ row.原始单位 }}</td>
              <td>{{ row.目标单位 }}（{{ row.目标量纲 }}）</td>
              <td>{{ row.换算系数 }}</td>
              <td>{{ row.零点偏移 }}</td>
              <td :class="row.量纲校验 === '冲突' ? 'error-text' : ''">
                {{ row.量纲校验 }}<template v-if="row.冲突原因"><br />{{ row.冲突原因 }}</template>
              </td>
              <td>
                <div v-for="trial in row.原始序列试算" :key="trial.timestamp" class="trial-line">
                  {{ trial.timestamp }}：原值 {{ trial.原始值 }} {{ trial.原始单位 }}
                  → 比例项 {{ trial.比例项_对齐前 }} + 偏移项 {{ trial.偏移项_对齐前 }}
                  = <strong>{{ trial.试算结果_对齐前 }} {{ trial.目标单位 }}</strong>
                </div>
                <span v-if="!row.原始序列试算.length" class="empty-state">该测点暂无原始样本</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <!-- 2. 分线规则与启用范围 -->
    <article class="cal-panel">
      <h3>② 分线规则视图（审批放行 / 启用资格 / 本次工单可启用范围）</h3>
      <div v-for="line in lineSummary" :key="line.产线" class="line-block">
        <header>
          <strong>{{ line.产线 }}（{{ line.测点编号 }}）</strong>
          <span>在役：{{ line.在役规则 ?? '—' }} · 目标单位 {{ line.在役目标单位 ?? '—' }}</span>
        </header>
        <table class="data-table">
          <thead>
            <tr><th>规则编号</th><th>目标单位</th><th>类型</th><th>审批状态</th><th>启用状态</th><th>判定 / 原因</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="rule in rulesFor(line.测点编号)" :key="rule.id" :data-status="rule.启用状态">
              <td>{{ rule.规则编号 }}</td>
              <td>{{ rule.目标单位 }}</td>
              <td>{{ rule.规则类型 }}</td>
              <td :class="rule.审批状态 === '冲突搁置' ? 'error-text' : ''">{{ rule.审批状态 }}</td>
              <td>{{ rule.启用状态 }}</td>
              <td>
                <template v-if="rule.冲突原因"><span class="error-text">{{ rule.冲突原因 }}</span></template>
                <template v-else-if="rule.启用状态 === '已启用'">在役，本次继续生效</template>
                <template v-else>
                  <button class="link" type="button" @click="checkEligibility(rule.id)">查看启用资格</button>
                </template>
              </td>
              <td class="row-actions">
                <button
                  v-if="rule.审批状态 === '待审核'"
                  class="link" type="button"
                  @click="approve(rule.id)"
                >审批放行</button>
                <button
                  v-if="rule.审批状态 === '已放行' && rule.启用状态 !== '已启用'"
                  class="link" type="button"
                  @click="enable(rule.id)"
                >分线启用</button>
              </td>
            </tr>
          </tbody>
        </table>
        <ul v-if="eligibilityText[line.测点编号]" class="eligibility-list">
          <li v-for="(text, idx) in eligibilityText[line.测点编号]" :key="idx">{{ text }}</li>
        </ul>
      </div>
    </article>

    <!-- 3. 对齐试读：请求时绑定版本，读数只采用一套 -->
    <article class="cal-panel">
      <h3>③ 对齐试读（请求先接受、读数后执行；同次读取只用绑定的一套换算）</h3>
      <form class="analysis-form" @submit.prevent="onSchedule">
        <label>测点
          <select v-model="trialPoint" data-testid="trial-point">
            <option value="PT-AIR">压缩空气线 PT-AIR</option>
            <option value="PT-WATER">水压线 PT-WATER</option>
          </select>
        </label>
        <label class="reason-input">
          试读原因
          <input v-model="trialReason" data-testid="trial-reason" />
        </label>
        <button class="btn primary" data-testid="schedule-trial" type="submit">接受试读请求</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>试读编号</th><th>测点</th><th>绑定规则(版本)</th><th>请求时间</th>
            <th>执行时间</th><th>读数时放行已生效</th><th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="trial in trials" :key="trial.id">
            <td>{{ trial.试读编号 }}</td>
            <td>{{ trial.测点编号 }}</td>
            <td>{{ trial.绑定规则编号 }}<br /><small>{{ trial.绑定规则版本时间 }}</small></td>
            <td>{{ trial.请求时间 }}</td>
            <td>{{ trial.执行时间 ?? '—' }}</td>
            <td>{{ trial.执行时间 ? (trial.放行生效_读数时 ? '是（仍按绑定规则出数）' : '否') : '—' }}</td>
            <td>{{ trial.status }}</td>
            <td class="row-actions">
              <button v-if="trial.status === '已接受'" class="link" type="button" @click="runTrial(trial.id)">
                执行读数
              </button>
              <button v-if="trial.status === '已执行'" class="link" type="button" @click="showReadings(trial.id)">
                查看每行读数
              </button>
              <button v-if="trial.status === '已执行'" class="link" type="button" @click="compare(trial.id)">
                核对试读/正式值
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="activeReadings.length" class="reading-block" data-testid="reading-detail">
        <h4>试读 {{ activeTrialNo }} 每行实际读数（数值 / 量纲 / 换算出处）</h4>
        <table class="data-table">
          <thead>
            <tr>
              <th>时刻</th><th>原始值</th><th>采用规则</th><th>采用值</th>
              <th>未采用备选（同样覆盖该观测）</th><th>换算出处</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="reading in activeReadings" :key="reading.timestamp">
              <td>{{ reading.timestamp }}</td>
              <td>{{ reading.实际读到原始值 }} {{ reading.原始单位 }}（{{ reading.原始量纲 }}）</td>
              <td>{{ reading.采用规则 }}</td>
              <td><strong>{{ reading.采用值 }} {{ reading.采用单位 }}（{{ reading.采用量纲 }}）</strong></td>
              <td>
                <div v-for="alt in reading.未采用备选" :key="alt.规则编号" class="trial-line">
                  {{ alt.规则编号 }}：{{ alt.试算结果_对齐前 }} {{ alt.目标单位 }}（未采用）
                </div>
                <span v-if="!reading.未采用备选.length" class="empty-state">无其他覆盖规则</span>
              </td>
              <td>{{ reading.换算出处 }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <!-- 4. 试读与正式值差异核对 -->
    <article v-if="diffs.length" class="cal-panel" data-testid="diff-panel">
      <h3>④ 试读值与正式值核对（{{ diffPoint }}）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>时刻</th><th>原始值(量纲)</th><th>试读值</th><th>正式值</th>
            <th>量纲/单位</th><th>差值(正式-试读)</th><th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in diffs" :key="d.timestamp">
            <td>{{ d.timestamp }}</td>
            <td>{{ d.原始值 }}（{{ d.原始量纲 }}）</td>
            <td>{{ d.试读值 }} {{ d.试读单位 }}<br /><small>{{ d.试读出处 }}</small></td>
            <td>{{ d.正式值 }} {{ d.正式单位 }}<br /><small>{{ d.正式出处 }}</small></td>
            <td>{{ d.量纲一致 ? '量纲一致' : '量纲不同' }} / {{ d.单位一致 ? '单位一致' : '单位不同' }}</td>
            <td>{{ d.差值_正式减试读 ?? '不可直接相减' }}</td>
            <td>{{ d.说明 }}</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { calibrationApi } from '@/api/calibration'
import type {
  AlignTrial,
  CalibrationRule,
  ImportBatch,
  LineSummary,
  TrialFormalDiff,
  TrialReading,
} from '@/data/types'

const csvPlaceholder = [
  '压缩空气线,PT-AIR,mV,kPa,0.5,0,续订',
  '水压线,PT-WATER,mV,V,0.001,0,续订',
].join('\n')

const imports = ref<ImportBatch[]>([])
const rules = ref<CalibrationRule[]>([])
const lineSummary = ref<LineSummary[]>([])
const trials = ref<AlignTrial[]>([])
const diffs = ref<TrialFormalDiff[]>([])
const diffPoint = ref('')
const activeReadings = ref<TrialReading[]>([])
const activeTrialNo = ref('')
const eligibilityText = ref<Record<string, string[]>>({})

const csvContent = ref(csvPlaceholder)
const sourceName = ref('换算表.csv')
const trialPoint = ref('PT-WATER')
const trialReason = ref('导入单位冲突后的对齐复核轮')
const busy = ref(false)
const message = ref('')
const hasError = ref(false)

function notice(text: string, isError = false) {
  message.value = text
  hasError.value = isError
}

async function refreshAll() {
  const [importRows, ruleRows, lines, trialRows] = await Promise.all([
    calibrationApi.listImports(),
    calibrationApi.listRules(),
    calibrationApi.lineSummary(),
    calibrationApi.listTrials(),
  ])
  imports.value = importRows.reverse()
  rules.value = ruleRows
  lineSummary.value = lines
  trials.value = trialRows
}

function rulesFor(point: string): CalibrationRule[] {
  return rules.value.filter((rule) => rule.测点编号 === point)
}

async function seedDemo() {
  try {
    const result = await calibrationApi.seedDemo()
    notice(result.message)
    await refreshAll()
  } catch (error) {
    notice(error instanceof Error ? error.message : '演示基线载入失败', true)
  }
}

async function onImport() {
  busy.value = true
  try {
    const result = await calibrationApi.importTable(csvContent.value, sourceName.value)
    notice(result.message)
    await refreshAll()
  } catch (error) {
    notice(error instanceof Error ? error.message : '换算表导入失败', true)
  } finally {
    busy.value = false
  }
}

async function approve(id: number) {
  try {
    const result = await calibrationApi.approveRule(id, '审批人')
    notice(result.message, !result.ok)
    await refreshAll()
  } catch (error) {
    notice(error instanceof Error ? error.message : '放行失败', true)
  }
}

async function checkEligibility(id: number) {
  const rule = rules.value.find((item) => item.id === id)
  if (!rule) return
  try {
    const verdict = await calibrationApi.ruleEligibility(id)
    eligibilityText.value[rule.测点编号] = [
      `${verdict.eligible ? '可启用' : '暂不可启用'}：${verdict.reasons.join('；')}`,
      ...verdict.notices,
    ]
  } catch (error) {
    notice(error instanceof Error ? error.message : '启用资格查询失败', true)
  }
}

async function enable(id: number) {
  try {
    const result = await calibrationApi.enableRule(id)
    notice(result.message, !result.ok)
    await refreshAll()
  } catch (error) {
    notice(error instanceof Error ? error.message : '启用失败', true)
  }
}

async function onSchedule() {
  try {
    const result = await calibrationApi.scheduleTrial(trialPoint.value, trialReason.value)
    if (!result.ok) throw new Error(result.message)
    notice(result.message)
    await refreshAll()
  } catch (error) {
    notice(error instanceof Error ? error.message : '试读请求失败', true)
  }
}

async function runTrial(id: number) {
  try {
    const result = await calibrationApi.runTrial(id)
    notice(result.message)
    await refreshAll()
    await showReadings(id)
  } catch (error) {
    notice(error instanceof Error ? error.message : '试读执行失败', true)
  }
}

async function showReadings(id: number) {
  const trial = trials.value.find((item) => item.id === id)
  if (!trial) return
  activeTrialNo.value = trial.试读编号
  activeReadings.value = trial.读数
}

async function compare(id: number) {
  try {
    const result = await calibrationApi.compareTrial(id)
    if (!result.ok) throw new Error(result.message ?? '核对失败')
    diffs.value = result.diffs
    diffPoint.value = result.point
  } catch (error) {
    notice(error instanceof Error ? error.message : '差异核对失败', true)
  }
}

onMounted(refreshAll)
</script>

<style scoped>
.calibration-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.cal-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}
.cal-panel h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.csv-input,
.reason-input {
  flex: 1;
  min-width: 260px;
}
.csv-input textarea {
  width: 100%;
  box-sizing: border-box;
  font-family: monospace;
  font-size: 12px;
}
.import-block,
.line-block,
.reading-block {
  margin-top: 12px;
}
.import-block header,
.line-block header {
  display: flex;
  gap: 14px;
  align-items: baseline;
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--muted);
}
.trial-line {
  font-size: 12px;
  line-height: 1.7;
}
.eligibility-list {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--muted);
}
tr[data-conflict='冲突'] td {
  background: #fef3f2;
}
.reading-block h4 {
  margin: 10px 0 6px;
  font-size: 13px;
}
</style>
