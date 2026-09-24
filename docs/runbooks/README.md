# 运行手册

本目录把评测流水线三段日常作业的操作步骤固化为可复用模板。手册只写"怎么跑、看什么、
失败了怎么办"，指标口径仍以根目录《初步入库评测指标.md》为准，架构与模块职责见
`docs/arch/code-map.md`。

| 手册 | 输入 | 产出 | 对应脚本 |
|---|---|---|---|
| [01-salvage-logs.md](01-salvage-logs.md) | uid + 北京时间日期 + 问题开头 | 本地 jsonl 日志 + `salvage_manifest.json` | `scripts/salvage_oss_logs.py` |
| [02-run-wide-table.md](02-run-wide-table.md) | 平台导出的 uid/session_id/msg_id 清单（xlsx） | `metric_table_full.csv` 宽表 | `scripts/stream_compute_metrics.py` |
| [03-generate-report.md](03-generate-report.md) | 宽表 CSV | 自包含 HTML 报告 | `.agents/skills/metric-report/scripts/generate_report.py` |

三段是接力关系：01 打捞单条异常日志做定因，02 按时间范围全量跑宽表做统计，03 把宽表
渲染成报告。三者互不依赖，可以单独执行。

## 共同前置

### 环境变量

连接配置只从 `.env`（仓库根，已 gitignore）或 CLI 参数读取。复制 `.env.example` 后填写：

```text
OSS_ENDPOINT=
OSS_BUCKET=
OSS_ROOT_PREFIX=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
JUDGE_API_URL=
JUDGE_API_KEY=
JUDGE_MODEL=
```

真实密钥禁止写入代码、文档或提交历史。01/02 只用到 `OSS_*`；只有跑 Judge 类指标
（参数值准确率、路由、意图、时间范围）才需要 `JUDGE_*`。

### 两个 Python 解释器

| 解释器 | 路径 | 用途 |
|---|---|---|
| 项目 venv | `.venv/Scripts/python.exe` | 全部 OSS 脚本、pytest、ruff、mypy；已装 `oss2` / `openpyxl` |
| anaconda | `C:/Users/EDY/anaconda3/python.exe` | 仅 03 报告（venv 未装 `matplotlib` / `numpy`） |

### Windows 中文乱码

脚本输出含中文，控制台必须带 `-X utf8`，否则报 `UnicodeEncodeError` 或输出乱码：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.salvage_oss_logs ...
```

### 数据落盘边界

OSS 脚本一律不把原始日志提交入库；下载目录、宽表、报告都在 `.gitignore` 覆盖范围内。
脚本中间产物（含真实 uid / session_id / msg_id）不要手工 `git add -f`。
