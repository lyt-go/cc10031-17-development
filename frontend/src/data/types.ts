/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 单位标定工单：换算规则、导入预览、对齐试读与差异核对的接口结构。 */
export type CalibrationRule = {
  id: number
  规则编号: string
  产线: string
  测点编号: string
  原始单位: string
  目标单位: string
  换算系数: number
  零点偏移: number
  规则类型: string
  审批状态: string
  启用状态: string
  冲突原因: string
  来源导入批次: string
  版本时间: string
  放行时间: string | null
  启用时间: string | null
  status: string
}

export type RuleTrialTerm = {
  规则编号: string
  规则类型: string
  审批状态: string
  启用状态: string
  换算系数: number
  零点偏移: number
  比例项_对齐前: number
  偏移项_对齐前: number
  试算结果_对齐前: number
  目标单位: string
  目标量纲: string
  换算出处: string
}

export type ImportPreviewRow = {
  行号: number
  规则编号: string
  产线: string
  测点编号: string
  原始单位: string
  目标单位: string
  目标量纲: string
  换算系数: number
  零点偏移: number
  规则类型: string
  量纲校验: string
  冲突原因: string
  原始序列试算: (RuleTrialTerm & { timestamp: string; 原始值: number; 原始单位: string; 原始量纲: string })[]
}

export type ImportBatch = {
  id: number
  批次编号: string
  来源文件: string
  导入时间: string
  总行数: number
  通过行数: number
  冲突行数: number
  冲突明细: { 行号: number; 规则编号?: string; 测点编号: string; 目标单位: string; 原因: string }[]
  预览: ImportPreviewRow[]
  status: string
}

export type Eligibility = {
  eligible: boolean
  reasons: string[]
  notices: string[]
  active_rule: string | null
}

export type LineSummary = {
  产线: string
  测点编号: string
  在役规则: string | null
  在役目标单位: string | null
  规则总数: number
  本次可启用: { 规则编号: string; 目标单位: string; 规则类型: string; 审批状态: string; 判定: string }[]
  本次不可启用: { 规则编号: string; 目标单位: string; 规则类型: string; 审批状态: string; 判定: string; 原因?: string }[]
}

export type TrialReading = {
  timestamp: string
  实际读到原始值: number
  原始量纲: string
  原始单位: string
  采用规则: string
  采用值: number
  采用量纲: string
  采用单位: string
  换算出处: string
  同次读取仅采用一套: boolean
  未采用备选: (RuleTrialTerm & { 是否采用: boolean; 是否覆盖该观测: boolean })[]
}

export type AlignTrial = {
  id: number
  试读编号: string
  测点编号: string
  产线: string
  绑定规则编号: string
  绑定规则版本时间: string
  请求时间: string
  执行时间: string | null
  放行生效_读数时: boolean | null
  请求原因: string
  读数: TrialReading[]
  status: string
}

export type TrialFormalDiff = {
  timestamp: string
  原始值: number
  原始量纲: string
  试读值: number
  试读单位: string
  试读出处: string
  正式值: number | null
  正式单位: string | null
  正式出处: string | null
  量纲一致: boolean
  单位一致: boolean
  差值_正式减试读: number | null
  说明: string
}

