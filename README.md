# 工业传感器时序采集与降采样平台

面向工业传感器高频时序数据的测点接入、边缘缓冲上送、单位标定、时间对齐、降采样归并与分层归档的采集平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 测点台账 | `sensor_point` | 测点档案 | 测点编号、所属产线、传感器型号 |
| 边缘缓冲 | `edge_buffer` | 缓冲分片 | 分片编号、测点编号、起始时刻 |
| 批量上送 | `upload_batch` | 上送批次 | 批次编号、分片范围、测点数量 |
| 单位标定 | `unit_calibrate` | 标定记录 | 标定编号、测点编号、原始单位 |
| 时间对齐 | `time_align` | 对齐规则 | 规则编号、对齐粒度、时区基准 |
| 断点填补 | `gap_fill` | 填补区间 | 区间编号、测点编号、缺失起点 |
| 尖峰剔除 | `spike_filter` | 异常采样 | 异常编号、测点编号、采样时刻 |
| 降采样 | `downsample` | 降采样任务 | 任务编号、源序列、源采样频率 |
| 窗口归并 | `rollup_window` | 归并窗口 | 窗口编号、测点编号、窗口类型 |
| 序列索引 | `series_index` | 索引分区 | 索引编号、测点编号、时间分区 |
| 冷热分层 | `tier_storage` | 分层策略 | 策略编号、时间边界、热区范围 |
| 采集心跳 | `device_health` | 心跳记录 | 心跳编号、采集终端、上报时刻 |
| 接入水位 | `ingest_watermark` | 水位标记 | 水位编号、测点编号、事件时间水位 |
| 区间重采 | `range_replay` | 重采任务 | 重采编号、测点编号、起始时刻 |
| 采集血缘 | `series_lineage` | 血缘链路 | 链路编号、测点编号、上送批次 |
| 序列导出 | `series_export` | 导出任务 | 导出编号、测点范围、时间范围 |
| 时序质量 | `series_quality` | 质量报告 | 报告编号、测点编号、统计窗口 |
| 序列快照 | `series_snapshot` | 版本快照 | 快照编号、口径说明、测点范围 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 单位标定工单（换算表导入 / 分线启用 / 对齐试读）

在通用单位标定模块之外，`backend/app/services/calibration_table.py` 提供工单级能力，
专用路由前缀 `/api/unit-calibration`，前端页面 `/unit-calibration`（导航「标定工单(换算表)」）：

- **表格导入与预览**：`POST /conversion-table/import` 接收 CSV（列：产线、测点编号、
  原始单位、目标单位、换算系数、零点偏移、规则类型），逐行做量纲校验，并用该测点
  原始序列试算「比例项（原值×系数）/ 偏移项」两项对齐前数值；目标量纲与产线工程量纲
  不一致的行置为「冲突搁置」，不允许放行，须修正后重新导入。
- **审批放行与分线启用**：`/rules/{id}/approve`、`/rules/{id}/enable`。一台测点任一时刻
  只允许一套在役换算，启用续订会自动停用旧规；未放行或冲突规则不可启用，审核前在役
  规则与正式值不变。`/lines/summary` 给出本次工单各规则的可启用范围。
- **对齐试读**：`POST /align-trials` 先接受请求并绑定当时在役规则版本，
  `POST /align-trials/{id}/run` 稍后真正读数。即使读数时新规则已经审批放行，同一次读取
  仍只采用绑定的一套，其他同样覆盖该观测的规则仅作未采用备选；每行读数都记录实际
  数值、量纲、单位与换算出处。
- **试读/正式核对**：`GET /align-trials/{id}/comparison` 逐行对比试读值与当前在役规则的
  正式值；量纲或单位不同（如 MPa 对 kPa）时不给数值差，只提示归一换算后再核。
- 演示可先调 `POST /demo-seed` 生成压缩空气线 PT-AIR、水压线 PT-WATER 的在役 MPa 旧规
  与含 300 mV 的原始样本；验收用例见 `backend/tests/test_calibration_table.py`。

领域：数据工程
子方向：数据采集、爬虫与RPA
