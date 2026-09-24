"""把工具错误归因(失败工具名 + 归因分类)回填进宽表。

只补一列 `tool_error_attribution`,只处理 `tool_error_rate` 单元格里
`error_call_count > 0` 的行(零错误行留空),其余列原样保留。逐行回 OSS 取流,
只调用 `normalize_research_tool_error_rate` + `ToolErrorRateEvaluator` 读
metadata 的 `error_calls`,不重算其它指标(省 CPU);断点续跑:新列已非空的行跳过。

口径(2026-09-24 需求方确认):
  - 只统计 error 终态的调用,degraded(code:1 自检降级)继续只进
    `degraded_result_count`、不带工具名入列——本列与 `error_call_count` 严格一对一;
  - by_tool / by_attribution **不做 Top-K 截断**(与 `api_retry_attribution` 的
    Top5 口径刻意不同):`error_call_count` 中位 2、p99 17、最大 59,截断只会砍掉
    撞墙最严重的长尾日志的尾部工具,而定位失败工具正是本列的唯一目的。

为什么需要本列:`ToolErrorRateEvaluator` 早已在 metadata 产出 `error_calls`,
但 `build_metric_table` 只提取 `partial_issues` / `partial_issue_reasons`,
逐次失败的工具名在入宽表时被静默丢弃,导致报告与 bad case 只能看到
"发生 N 次工具错误"却定位不到是哪个工具。

用法:
  python -X utf8 -m scripts.backfill_tool_error_attribution \
      --csv data/processed/metric_table_full.csv --workers 10
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from collectors.tool_error_rate import normalize_research_tool_error_rate
from evaluators.tool_error_rate import ToolErrorRateEvaluator
from scripts.export_oss_jsonl import build_prefix, load_env_file, make_oss_bucket
from scripts.stream_compute_metrics import (
    StreamBucketLike,
    _parse_events,
    select_query_version,
)
from scripts.summarize_metric_table import build_tool_error_attribution_cell

NEW_COL = "tool_error_attribution"

# 单元格构造与 summarize 共用单一实现,避免两处漂移;此处 re-export 供测试引用。
build_cell = build_tool_error_attribution_cell


def fetch_attribution(
    bucket: StreamBucketLike, uid: str, ses: str, msg: str, root_prefix: str
) -> str:
    """回 OSS 取单条日志的工具错误归因;取不到/解析失败返回空串(该行保持空)。"""

    prefix = build_prefix(root_prefix, uid, ses, msg)
    try:
        keys = sorted(o.key for o in bucket.list_objects(prefix).object_list)
        if not keys:
            return ""
        key = select_query_version(keys)
        if key is None:
            return ""
        events = _parse_events(bucket.get_object(key))
    except Exception:  # noqa: BLE001 - 单条失败隔离,保持该列为空
        return ""
    normalized = normalize_research_tool_error_rate(events, session_id=ses, user_message_id=msg)
    result = ToolErrorRateEvaluator().evaluate(normalized)
    if result.status.value != "computed":
        return ""
    return build_cell(result.metadata.get("error_calls"))


def main() -> None:
    load_env_file(Path(__file__).resolve().parent.parent / ".env")
    ap = argparse.ArgumentParser(description="回填 tool_error_attribution 列")
    ap.add_argument("--csv", type=Path, default=Path("data/processed/metric_table_full.csv"))
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0, help=">0 时只处理前 N 个待补行(调试)")
    args = ap.parse_args()

    with args.csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if NEW_COL not in fieldnames:
        # 新列紧跟 tool_error_rate 之后,便于与值列对照阅读;不存在则追加到末尾。
        idx = (
            fieldnames.index("tool_error_rate") + 1
            if "tool_error_rate" in fieldnames
            else len(fieldnames)
        )
        fieldnames.insert(idx, NEW_COL)
        for r in rows:
            r[NEW_COL] = ""

    pending = []
    for i, r in enumerate(rows):
        cell = r.get("tool_error_rate") or ""
        if not cell or r.get(NEW_COL):
            continue  # 不可计算 / 无错误 / 已回填
        try:
            count = json.loads(cell)[0]["error_call_count"]
        except Exception:
            continue
        if count <= 0:
            continue
        pending.append((i, r))
    if args.limit:
        pending = pending[: args.limit]
    print(f"总行 {len(rows)}, 待补 {len(pending)}", file=sys.stderr)

    bucket = make_oss_bucket(os.environ["OSS_ENDPOINT"], os.environ["OSS_BUCKET"])
    rp = os.environ["OSS_ROOT_PREFIX"]
    filled = 0

    def work(item: tuple[int, dict[str, str]]) -> tuple[int, str]:
        i, r = item
        return i, fetch_attribution(bucket, r["user_id"], r["session_id"], r["log_id"], rp)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, it) for it in pending]
        for fut in as_completed(futs):
            i, cell = fut.result()
            rows[i][NEW_COL] = cell
            filled += 1
            if filled % 100 == 0:
                print(f"进度 {filled}/{len(pending)}", file=sys.stderr)

    # 原子替换:写临时文件再 rename,防半写损坏宽表
    fd, tmp = tempfile.mkstemp(dir=args.csv.parent, suffix=".tmp")
    os.close(fd)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, args.csv)
    nonempty = sum(1 for r in rows if r.get(NEW_COL))
    print(f"回填完成: 处理 {filled}, 新列非空 {nonempty}, 已写 {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
