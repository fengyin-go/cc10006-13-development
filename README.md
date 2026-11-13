# 数据湖仓一体管理平台

面向数据湖仓一体架构下的多源异构接入、原始文件编目、湖内分区治理、流批一体转换、版本回溯与数据编目检索的管理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
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
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据编目 | `catalog` | 数据目录 | 编目编号、数据集名称、存储路径 |
| 多源接入 | `ingest` | 接入通道 | 通道编号、源端类型、连接参数 |
| 原始层管理 | `bronze` | 原始表 | 表编号、来源通道、目标路径 |
| 清洗层管理 | `silver` | 清洗表 | 表编号、上游原始表、清洗脚本 |
| 聚合层管理 | `gold` | 聚合视图 | 视图编号、上游清洗表、聚合维度 |
| 湖区分区 | `lakezone` | 分区方案 | 方案编号、目标表名、分区字段 |
| 流式处理 | `stream` | 流处理作业 | 作业编号、输入Topic、处理逻辑 |
| 批量转换 | `batch` | 批处理任务 | 任务编号、输入路径、处理脚本 |
| 版本快照 | `snapshot` | 快照记录 | 快照编号、目标表名、快照时间 |
| 湖仓治理 | `govern` | 治理规则 | 规则编号、治理对象、治理类型 |
| 元数据中心 | `meta` | 元数据条目 | 条目编号、对象类型、对象名称 |
| 血缘图谱 | `lineage` | 血缘节点 | 节点编号、节点类型、节点名称 |
| 质量监控 | `quality` | 质量指标 | 指标编号、监控对象、指标类型 |
| 编排调度 | `schedule` | 编排计划 | 计划编号、任务列表、依赖图定义 |
| 历史回填 | `backfill` | 回填作业 | 作业编号、目标表名、回填起始 |
| 数据检索 | `search` | 检索索引 | 索引编号、索引名称、覆盖表名 |
| 资源计量 | `resource` | 计量记录 | 记录编号、计算引擎、作业名称 |
| 操作审计 | `audit` | 审计日志 | 日志编号、操作类型、操作对象 |

## 版本快照恢复

快照记录除了登记与生效，还提供依赖链校验与恢复能力，规则都在
`backend/app/services/snapshot.py`：

- 状态只能单向流转：`草稿 → 待创建 → 已生效 → 已过期`，回退与跳级都会被拦下；
  重复执行同一动作幂等。
- `恢复校验` 也只能 `待校验 → 校验通过`。依赖快照链上任一缺失或未生效时，
  校验只返回缺失清单，不会推进状态。
- `生效快照` 要求依赖链全部已生效且恢复校验通过；否则返回
  `missing_dependencies` 并阻止生效。
- `恢复快照` 在生效基础上，还会检查目标表的聚合视图与回填作业：
  已启用/待校验的聚合视图、待执行/运行中/待同步的回填作业都会阻止恢复，
  阻止原因放在 `blockers`。
- 恢复成功会写一条目标表版本变更与审计记录，并生成恢复回执；并发恢复只成功一次，
  重复请求返回同一张回执（`duplicate: true`，目标表不再变更）。
- 已过期的快照保留记录、回执与审计，但不能再参与生效或恢复。

示例数据里 `SNAP-0001 → SNAP-0002 → SNAP-0003` 是一条三级链：
`SNAP-0002` 待创建、`SNAP-0003` 已生效，直接生效 `SNAP-0001` 会列出缺失的 `SNAP-0002`；
`SNAP-0002` 校验并生效后，`SNAP-0001` 才能按链推进。`dws_order_agg` 上有一条
`BACK-0004` 待同步回填作业，完成同步前恢复会被阻止。

相关接口：

- `POST /api/snapshot/{id}/actions`：动作包含 `提交创建`、`校验恢复`、`生效快照`、`恢复快照`、`标记过期`
- `POST /api/snapshot/{id}/verify`：等价于 `校验恢复`
- `POST /api/snapshot/{id}/restore`：等价于 `恢复快照`
- `GET /api/snapshot/receipts`、`GET /api/snapshot/receipts/{id}`：恢复回执
- `GET /api/snapshot/targets/{表名}`：目标表当前快照版本

测试：`cd backend && .venv/bin/python -m pytest tests`。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖
