"""把框架层 API 重试归因(by_tool / by_endpoint / retry_after_success)回填进宽表。

只补一列 `api_retry_attribution`,只处理 api_retry_rate > 0 的行(零重试行留空),
其余 21 列原样保留。逐行回 OSS 取流,只调用 normalize_research_api_retry 读归因,
不重算其它指标(省 CPU);断点续跑:新列已非空的行跳过。

口径(2026-09-24 需求方确认):归因仅框架层(同 callID 内同指纹重复),
不含模型层跨 callID 重复;by_tool/by_endpoint 取 Top5(按次数降序)。

用法:
  python -X utf8 -m scripts.backfill_api_retry_attribution \
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

from collectors.api_retry import normalize_research_api_retry
from scripts.export_oss_jsonl import build_prefix, load_env_file, make_oss_bucket
from scripts.stream_compute_metrics import (
    StreamBucketLike,
    _parse_events,
    select_query_version,
)
from scripts.summarize_metric_table import build_attribution_cell

NEW_COL = "api_retry_attribution"
TOP_K = 5

# 单元格构造与 summarize 共用单一实现,避免两处漂移;此处 re-export 供测试引用。
# `__all__` 显式声明 re-export,否则 strict mypy 会把 `_topk` 判为未导出属性。
build_cell = build_attribution_cell
from scripts.summarize_metric_table import _topk  # noqa: E402, F401

__all__ = ["NEW_COL", "TOP_K", "_topk", "build_cell", "fetch_attribution", "main"]


def fetch_attribution(
    bucket: StreamBucketLike, uid: str, ses: str, msg: str, root_prefix: str
) -> dict[str, object] | None:
    """回 OSS 取单条日志的归因;取不到/解析失败返回 None(该行保持空)。"""
    prefix = build_prefix(root_prefix, uid, ses, msg)
    try:
        keys = sorted(o.key for o in bucket.list_objects(prefix).object_list)
        if not keys:
            return None
        key = select_query_version(keys)
        if key is None:
            return None
        events = _parse_events(bucket.get_object(key))
    except Exception:  # noqa: BLE001 - 单条失败隔离,保持该列为空
        return None
    norm = normalize_research_api_retry(events)
    return norm.get("api_retry_attribution")


def main() -> None:
    load_env_file(Path(__file__).resolve().parent.parent / ".env")
    ap = argparse.ArgumentParser(description="回填 api_retry_attribution 列")
    ap.add_argument("--csv", type=Path, default=Path("data/processed/metric_table_full.csv"))
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0, help=">0 时只处理前 N 个待补行(调试)")
    args = ap.parse_args()

    with args.csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if NEW_COL not in fieldnames:
        # 新列紧跟 api_retry_rate 之后,便于阅读;不存在则追加到末尾。
        idx = (
            fieldnames.index("api_retry_rate") + 1
            if "api_retry_rate" in fieldnames
            else len(fieldnames)
        )
        fieldnames.insert(idx, NEW_COL)
        for r in rows:
            r[NEW_COL] = ""

    pending = []
    for i, r in enumerate(rows):
        cell = r.get("api_retry_rate") or ""
        if not cell or r.get(NEW_COL):
            continue  # 不可计算 / 零重试 / 已回填
        try:
            rate = json.loads(cell)[0]["api_retry_rate"]
        except Exception:
            continue
        if rate <= 0:
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
        attr = fetch_attribution(bucket, r["user_id"], r["session_id"], r["log_id"], rp)
        return i, build_cell(attr)

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
