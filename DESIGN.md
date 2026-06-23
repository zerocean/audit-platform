# audit-platform 设计文档

## 版本: v3.0
## 日期: 2026-06-18

---

## 1. 项目概述

### 1.1 定位

员工端工具平台，面向审计师/税务师执行具体业务。包含两个工具：
- **审计复核 (audit)**: PDF 报告 → Vision Parser 解析 → LLM 数值复核 + Inspector 语法检查
- **税务填表 (TaxFill)**: FS + TaxComp PDF → Vision Parser → Filling Engine → Excel 填表参考

### 1.2 架构

```
React 前端 (Vite + React Router)
    ↕ REST API + SSE
FastAPI 后端 (8767)
    ↕ SQLAlchemy
共享数据库 (shared_db, PostgreSQL/SQLite)
    ↕ 同表读写
admin-dashboard (5004) — 管理端统计监控
```

---

## 2. 页面清单

| 页面 | 路径 | 说明 |
|------|------|------|
| 登录 | `/login` | JWT 鉴权 |
| 首页 | `/` | 工具选择：审计复核 / 税务填表 |
| 审计工具 | `/audit` | 上传 PDF → 解析 → 复核 → 导出报告 |
| 税务工具 | `/taxfill` | 上传 2 个 PDF → 填表参考下载 |
| 任务管理 | `/tasks` | 历史任务列表，点入查看详情 |

---

## 3. 审计任务流程

```
上传 PDF
    ↓
vision_parser (串行, qwen3.5-omni-flash)
    ↓
audit_llm (并行) + inspector (并行)
    ├── 数值复核 (deepseek-v4-flash, SSE 流式)
    └── 语法检查 (qwen3.6-flash)
    ↓
status = "parsed" → status = "audited" → status = "success"
completed_at 仅在 status="success" 时设置
```

### 3.1 时序

- `completed_at` 仅在全部步骤完成后写入（inspector + audit_llm 都完成）
- SSE handler 设 `status="audited"`，inspector 完成后检查并升级为 `"success"`
- 反向也成立：inspector 先完成时不设 success，等 SSE 完成后检测到 inspector 已存在再设

---

## 4. TaxFill 任务流程

```
上传 FS PDF + TaxComp PDF (各一份)
    ↓
vision_parser (串行, qwen3.6-flash)
    ↓
filling_engine (串行, deepseek-v4-flash)
    ↓
输出: filling_reference.xlsx + .json + .ton
status = "success" (同步流水线，完成即设)
```

---

## 5. 环境配置

### 5.1 环境变量 (.env)

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | 百炼 API Key | - |
| `VISION_MODEL` | Vision 模型 | qwen3.6-flash |
| `AUDIT_MODEL` | 审计 LLM 模型 | deepseek-v4-flash |
| `INSPECTOR_MODEL` | Inspector 模型 | qwen3.6-plus |
| `ENV` | 环境标识 | (空=开发) production=生产 |
| `INTERNAL_API_KEY` | 内部 API 密钥 | - |
| `OSS_ACCESS_KEY_ID` | OSS AK | - |
| `OSS_ACCESS_KEY_SECRET` | OSS SK | - |
| `OSS_ENDPOINT` | OSS Endpoint | oss-cn-shenzhen.aliyuncs.com |
| `OSS_BUCKET` | OSS Bucket | audit-ha-bucket |
| `OSS_PREFIX` | OSS 前缀 | ai_workspace |

### 5.2 环境隔离

`ENV=production` 时启用 `TRACKING_ENABLED`，TaskStep/TaskFile 正常写入。本地开发 `source="development"`，不污染线上统计。

---

## 6. API 端点

### 6.1 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录，返回 JWT token |
| - | `Authorization: Bearer <token>` | 所有 API 请求头 |

401 时前端自动清除 token 跳转登录页。

### 6.2 审计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/audit/pdf` | 上传 PDF，执行 vision_parser |
| POST | `/api/audit/json` | SSE 流式数值复核 |
| POST | `/api/audit/json/sync` | 同步审计（供 email_worker） |
| POST | `/api/audit/inspector` | 语法检查，需传 task_id |
| GET | `/api/audit/download-upload/{id}` | 下载上传的 PDF |

### 6.3 税务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/taxfill/pipeline` | 全流水线 |
| GET | `/api/taxfill/download/{run_id}/{filename}` | 下载输出文件 |

### 6.4 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{id}` | 任务详情 |
| PUT | `/api/tasks/{id}/inspector` | 保存 inspector 结果 |

---

## 7. Token 追踪

所有 API 调用（vision_parser, audit_llm, inspector, filling_engine）记录完整的 `usage`：
- `prompt_tokens` → `input_tokens`
- `completion_tokens` → `output_tokens`
- `total_tokens`

费用计算 `compute_task_cost(input, output, provider, model)` 按 input/output 分别乘单价。

---

## 8. 文件存储

### 8.1 OSS 模式（生产环境）

配置 `OSS_ACCESS_KEY_ID` + `OSS_ACCESS_KEY_SECRET` 后自动启用：

```
ai_workspace/
  audit/{task_id}/input_report.pdf
  taxfill/{task_id}/fs_report.pdf
  taxfill/{task_id}/tax_computation.pdf
  taxfill/{task_id}/filling_reference.xlsx
  taxfill/{task_id}/filling_reference.json
```

下载时生成预签名 URL（1小时有效）重定向。

### 8.2 本地回退

OSS 未配置时文件存在 `outputs/uploads/{task_id}/`。

---

## 9. email_worker

IMAP 轮询 163 邮箱，检测关键词触发任务：
- 邮件含"复核"或"review" + PDF 附件 → 审计复核
- 邮件含"tax return" + 2 个 PDF 附件 → 税务填表

关键配置：
- `.env` 优先脚本目录，回退 `/opt/.env`
- 163 IMAP 需先发 `imap.id()` 命令再 `openBox`
- `x-internal-key` header 鉴权调用 audit-platform API

---

## 10. 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 18, Vite 5, React Router 6, Lucide |
| 后端 | FastAPI, SQLAlchemy, python-jose (JWT) |
| 数据库 | PostgreSQL (生产) / SQLite (本地) |
| 存储 | 阿里云 OSS (生产) / 本地磁盘 (回退) |
| 邮件 | node-imap, nodemailer (独立 Node.js 进程) |
| 模型 | DashScope (qwen3.6-flash, qwen3.5-omni-flash, deepseek-v4-flash) |
