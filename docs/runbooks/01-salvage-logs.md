# 01 按 uid + 日期 + 问题打捞日志

用户报障（"某天我问了 XX，结果不对"）时，用这个流程把那条日志从 OSS 捞到本地。

脚本：`scripts/salvage_oss_logs.py`
入口：`python -X utf8 -m scripts.salvage_oss_logs`

## 输入

| 项 | 来源 | 说明 |
|---|---|---|
| uid | 管理后台人工查询 | 用户报的是手机号；手机号 → uid 的映射不在本项目内，先人工查好 |
| date | 用户口述 | **北京时间**日期，格式 `YYYY-MM-DD` |
| query | 用户口述 | 问题开头文本，可选；不传则列出当天全部 |

## 运行

```powershell
# 第 1 步：只列候选清单（默认不下载），肉眼确认哪条是目标日志
.\.venv\Scripts\python.exe -X utf8 -m scripts.salvage_oss_logs `
  --uid 3694996 --date 2026-09-10

# 第 2 步：加 --query 收窄（文件名 query 可能被截断，给开头片段即可）
.\.venv\Scripts\python.exe -X utf8 -m scripts.salvage_oss_logs `
  --uid 3694996 --date 2026-09-10 --query "帮我分析一下"

# 第 3 步：确认真实的候选后再下载
.\.venv\Scripts\python.exe -X utf8 -m scripts.salvage_oss_logs `
  --uid 3694996 --date 2026-09-10 --query "帮我分析一下" --download
```

多环境 / 多用户：

```powershell
# prod（默认）与 test 两个环境同时捞，候选清单带环境列，落盘按 <env>/ 分层
--env both

# 同一日期捞多个用户，--uid 可重复传
--uid 3694996 --uid 4210151
```

## 过滤逻辑（为什么有时捞不到）

脚本按文件名解析出 `(msg_id, query 开头, UTC 时间戳)`，**文件名时间戳是 UTC**，
而用户报的是北京时间，脚本按 `[前一天 16:00Z, 当天 16:00Z)` 换算日期窗口。

query 走三级匹配，逐级放宽：

1. **开头双向匹配** —— 归一化（去空白 / 标点 / 大小写）后双向比对开头。用户只记得
   片段、或文件名被截断，都能命中。
2. **包含 + 截断重叠 ≥ 4 字** —— 定时任务的文件名带统一前缀（`【定时任务执行】任务名称:`），
   用户报的任务名在中间，靠这一级命中。
3. **当天全量回退 `date_only`** —— 前两级都不中时，返回当天全部候选并标记 `date_only`。

第 3 级是有意为之：用户记忆的措辞与实际 query 常有出入，宁可多列一批让人工挑，
也不要空手而归。看到 `date_only` 标记说明日期过滤生效了但文本没匹配上。

命中 `msg_id` 后，同 `msg_id` 的 `_noquery` 版本会一并带出——异常定因需要全量版本。

## 产出

```text
data/oss_salvage/[<env>/]<uid>/<session>/<原文件名>
data/oss_salvage/salvage_manifest.json
```

- 目录默认 `data/oss_salvage`（gitignored），`--output` 可改。
- 指定环境时按 `<env>/` 分层，避免 prod 与 test 的同名文件混淆。
- 断点续传：已存在且非空的文件跳过，重跑安全。
- 单 uid 列举失败只隔离该 uid，不中断多 uid 批处理；失败信息进 manifest。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 候选全空，且不是 `date_only` | uid 错、日期跨界（凌晨的问题可能落在前一天 UTC），或环境选错——试 `--env both` |
| 只有 `date_only` 候选 | query 措辞差异过大；从清单里人工挑 `session_id` / `msg_id` |
| 报 `UnicodeEncodeError` | 漏了 `-X utf8` |
| 报缺少 endpoint / bucket | `.env` 没配 `OSS_*`，或没从仓库根运行 |

## 下一步

捞到日志后：单条定因直接读 JSONL；要进统计口径则把 `(uid, session_id, msg_id)`
补进清单，走 [02-run-wide-table.md](02-run-wide-table.md)。
