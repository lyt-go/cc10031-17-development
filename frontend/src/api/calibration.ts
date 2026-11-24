import { request } from './client'
import type {
  AlignTrial,
  CalibrationRule,
  Eligibility,
  ImportBatch,
  LineSummary,
  TrialFormalDiff,
} from '@/data/types'

const BASE = '/api/unit-calibration'

async function post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await request(`${BASE}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

async function get<T>(path: string): Promise<T> {
  const response = await request(`${BASE}${path}`)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export type ImportResult = ImportBatch & {
  ok: boolean
  message: string
  batch_id: number
  批次编号: string
  conflicts: ImportBatch['冲突明细']
  scheduled_trials: { ok: boolean; trial_id: number; 试读编号: string; 绑定规则编号: string }[]
}

export const calibrationApi = {
  seedDemo: () => post<{ ok: boolean; message: string }>('/demo-seed'),

  importTable: (content: string, sourceName: string) =>
    post<ImportResult>('/conversion-table/import', { content, source_name: sourceName }),

  listImports: () => get<ImportBatch[]>('/conversion-table/imports'),
  getImport: (id: number) => get<ImportBatch>(`/conversion-table/imports/${id}`),

  listRules: (point?: string) =>
    get<CalibrationRule[]>(`/rules${point ? `?point=${encodeURIComponent(point)}` : ''}`),
  approveRule: (id: number, approver: string) =>
    post<{ ok: boolean; message: string }>(`/rules/${id}/approve`, { approver }),
  ruleEligibility: (id: number) => get<Eligibility>(`/rules/${id}/eligibility`),
  enableRule: (id: number) =>
    post<{ ok: boolean; message: string; replaced_rule?: string | null }>(`/rules/${id}/enable`),

  lineSummary: () => get<LineSummary[]>('/lines/summary'),

  scheduleTrial: (point: string, reason: string) =>
    post<{ ok: boolean; message: string; trial_id: number; 试读编号: string }>('/align-trials', {
      point,
      reason,
    }),
  runTrial: (id: number) =>
    post<{ ok: boolean; message: string; trial: AlignTrial }>(`/align-trials/${id}/run`),
  listTrials: (point?: string) =>
    get<AlignTrial[]>(`/align-trials${point ? `?point=${encodeURIComponent(point)}` : ''}`),

  formalValues: (point: string) =>
    get<{ ok: boolean; message?: string; records: number; source_rule: string }>(
      `/formal-values/${encodeURIComponent(point)}`,
    ),
  compareTrial: (id: number) =>
    get<{ ok: boolean; message?: string; trial: string; point: string; diffs: TrialFormalDiff[] }>(
      `/align-trials/${id}/comparison`,
    ),
}
