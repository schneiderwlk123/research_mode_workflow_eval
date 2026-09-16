"""流式计算研究模式全量指标:OSS 日志不落地,边取流边算,只写结果 CSV。

背景:全量清单(11 万行,去重后约 6 千个 msg)对应的 jsonl 日志总量数十 GB,
批量下载到本地不可行。本脚本对每个 msg_id:
  1. 按 <根前缀>/<uid>/<session_id>/<msg_id> list OSS 对象;
  2. 选"带 query 版"(文件名不含 _noquery_)中时间戳最新的一个,仅有 noquery 版时跳过并记录;
  3. get_object 取流,在内存逐行解析事件,复用 summarize_metric_table 的 METRIC_COLUMNS
     算出一行指标,立即追加写入结果 CSV,原始日志不写本地磁盘;
  4. 单条失败(list/流读取/解析/计算)隔离为 issues.jsonl 中的诊断记录,不中断整批。

支持断点续跑:结果 CSV 中已存在的 log_id 自动跳过,失败后直接重跑即可只补未完成部分。

连接配置与 scripts/export_oss_jsonl.py 相同(CLI 参数 + OSS_* 环境变量 / 项目根 .env)。

用法示例:
  python -m scripts.stream_compute_metrics \
      --excel data/tmp_export/xxx.xlsx \
      --output data/processed/metric_table_full.csv \
      --workers 8
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Protocol

from reporters.metric_table import build_metric_table
from scripts.export_oss_jsonl import (
    build_prefix,
    load_env_file,
    make_oss_bucket,
    read_rows,
)
from scripts.summarize_metric_table import METRIC_COLUMNS, build_research_record

# 结果 CSV 的固定表头:身份列 + 已注册指标列(与批量入口 summarize_metric_table 一致)
_FIELDNAMES: tuple[str, ...] = (
    "user_id",
    "session_id",
    "log_id",
    "mode",
    *(column.name for column in METRIC_COLUMNS),
)


class StreamBucketLike(Protocol):
    """oss2.Bucket 的流式最小协议,便于测试注入内存假对象。"""

    def list_objects(self, prefix: str) -> Any: ...

    def get_object(self, key: str) -> Any: ...


def dedupe_tasks(rows: Sequence[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """全量清单重复率极高(11 万行 -> 约 6 千唯一),保序去重避免重复取流计算。"""
    return list(dict.fromkeys(rows))


def select_query_version(keys: Sequence[str]) -> str | None:
    """从前缀匹配到的对象中选"带 query 版"里时间戳最新的一个。

    口径(用户确认):文件名含 _noquery_ 的是无查询文本版本,计算只用带 query 版;
    同版本多时间戳视为多次记录,取最新。仅有 noquery 版时返回 None,由调用方记为跳过。
    """
    query_keys = [k for k in keys if "_noquery_" not in Path(k).name]
    if not query_keys:
        return None
    # 文件名以 _<ISO 时间戳>.jsonl 结尾,字典序即时间序
    return max(query_keys, key=lambda k: k.rsplit("_", 1)[-1])


def _parse_events(stream: Any) -> list[Any]:
    """把 OSS 字节流逐行解析为事件列表;非法行跳过,不中断整条日志。

    oss2 的 GetObjectResult 不是完整 IO 对象(缺 readable() 等),无法用
    TextIOWrapper 包装,因此按块 read 后手动分行解码;内存中只保留
    当前未完成行,不会整文件驻留。
    """
    events: list[Any] = []
    buffer = b""
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        buffer += chunk
        *complete_lines, buffer = buffer.split(b"\n")
        for raw in complete_lines:
            line = raw.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line.decode("utf-8")))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # 单行损坏不应丢弃整条日志;缺失影响由指标层的覆盖检查兜底
    tail = buffer.strip()
    if tail:
        try:
            events.append(json.loads(tail.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return events


def _compute_one(
    bucket: StreamBucketLike,
    task: tuple[str, str, str],
    root_prefix: str,
) -> dict[str, Any]:
    """取流并计算单条任务,返回 {kind, ...};任何异常都被隔离,不向上抛出。

    kind 取值:ok(携带 row/metric_issues)、missing、no_query_version、error。
    """
    uid, session_id, msg_id = task
    prefix = build_prefix(root_prefix, uid, session_id, msg_id)
    try:
        keys = sorted(o.key for o in bucket.list_objects(prefix).object_list)
        if not keys:
            return {"kind": "missing", "task": task, "error": f"OSS 无匹配对象, prefix={prefix}"}
        key = select_query_version(keys)
        if key is None:
            return {
                "kind": "no_query_version",
                "task": task,
                "error": f"仅有 noquery 版本, candidates={keys}",
            }
        events = _parse_events(bucket.get_object(key))
    except Exception as exc:  # noqa: BLE001 - 单条失败隔离,保证整批继续
        return {"kind": "error", "task": task, "error": f"{type(exc).__name__}: {exc}"}

    try:
        # 日志内提取不到 userMessageID 时回退用清单中的 msg_id,保证行可追溯
        record = build_research_record(events, fallback_id=msg_id)
        table = build_metric_table([record], METRIC_COLUMNS)
    except Exception as exc:  # noqa: BLE001 - 计算失败同样隔离
        return {"kind": "error", "task": task, "key": key, "error": f"计算失败: {exc}"}
    return {
        "kind": "ok",
        "task": task,
        "key": key,
        "row": table.rows[0],
        "metric_issues": [
            {
                "kind": "metric_issue",
                "log_id": issue.log_id,
                "metric_name": issue.metric_name,
                "status": issue.status,
                "reason": issue.reason,
            }
            for issue in table.issues
        ],
    }


def _load_done_log_ids(output_csv: Path) -> set[str]:
    """读取已有结果 CSV 的 log_id 集合,用于断点续跑跳过。"""
    if not output_csv.is_file():
        return set()
    with output_csv.open(encoding="utf-8", newline="") as stream:
        return {row["log_id"] for row in csv.DictReader(stream) if row.get("log_id")}


def stream_compute_all(
    bucket: StreamBucketLike,
    tasks: Sequence[tuple[str, str, str]],
    root_prefix: str,
    output_csv: Path,
    workers: int = 8,
    retries: int = 2,
) -> dict[str, int]:
    """并发流式计算全部任务,增量写结果 CSV 与诊断 JSONL,返回运行摘要。"""
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    issues_path = output_csv.parent / "issues.jsonl"

    done_ids = _load_done_log_ids(output_csv)
    pending = [t for t in dedupe_tasks(list(tasks)) if t[2] not in done_ids]
    skipped_done = len(dedupe_tasks(list(tasks))) - len(pending)

    summary = {
        "total": len(dedupe_tasks(list(tasks))),
        "skipped_done": skipped_done,
        "ok": 0,
        "error": 0,
        "missing": 0,
        "no_query_version": 0,
    }

    write_lock = threading.Lock()
    csv_is_new = not output_csv.is_file()
    csv_stream = output_csv.open("a", encoding="utf-8", newline="")
    writer = csv.DictWriter(csv_stream, fieldnames=_FIELDNAMES, extrasaction="raise")
    if csv_is_new:
        writer.writeheader()
        csv_stream.flush()
    issues_stream = issues_path.open("a", encoding="utf-8")
    started = time.monotonic()

    def _handle(result: dict[str, Any]) -> None:
        """在写锁内把单条结果落盘并更新摘要,保证 CSV/JSONL 行完整不交错。"""
        task = result["task"]
        if result["kind"] == "ok":
            writer.writerow(result["row"])
            csv_stream.flush()
            summary["ok"] += 1
        else:
            summary[result["kind"]] = summary.get(result["kind"], 0) + 1
        diagnostics: list[dict[str, Any]] = []
        if result["kind"] != "ok":
            diagnostics.append(
                {
                    "kind": result["kind"],
                    "uid": task[0],
                    "session_id": task[1],
                    "msg_id": task[2],
                    "error": result.get("error", ""),
                }
            )
        diagnostics.extend(result.get("metric_issues", []))
        for item in diagnostics:
            issues_stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        issues_stream.flush()
        finished = summary["ok"] + summary["error"] + summary["missing"] + summary[
            "no_query_version"
        ]
        if finished % 100 == 0 or finished == len(pending):
            elapsed = time.monotonic() - started
            rate = finished / elapsed if elapsed > 0 else 0.0
            eta_min = (len(pending) - finished) / rate / 60 if rate > 0 else -1
            print(
                f"进度: {finished}/{len(pending)} 速率: {rate:.2f} 条/秒 "
                f"预计剩余: {eta_min:.1f} 分钟 当前摘要: {summary}",
                file=sys.stderr,
            )

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for task in pending:

                def _run(t: tuple[str, str, str] = task) -> dict[str, Any]:
                    # 网络瞬态失败重试 retries 次;仍失败则按 error 隔离,可续跑补齐
                    last = _compute_one(bucket, t, root_prefix)
                    attempt = 0
                    while last["kind"] == "error" and attempt < retries:
                        attempt += 1
                        time.sleep(min(2**attempt, 8))
                        last = _compute_one(bucket, t, root_prefix)
                    return last

                futures[pool.submit(_run)] = task
            for fut in as_completed(futures):
                with write_lock:
                    _handle(fut.result())
    finally:
        csv_stream.close()
        issues_stream.close()

    print(f"完成: {summary}, 结果: {output_csv}, 诊断: {issues_path}", file=sys.stderr)
    return summary


def main() -> None:
    load_env_file(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser(description="流式计算研究模式全量指标(日志不落盘)")
    parser.add_argument("--excel", required=True, help="全量清单 xlsx(uid/session_id/msg_id)")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("OSS_ENDPOINT", ""),
        help="OSS endpoint(默认读 OSS_ENDPOINT)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("OSS_BUCKET", ""),
        help="OSS bucket 名(默认读 OSS_BUCKET)",
    )
    parser.add_argument(
        "--root-prefix",
        default=os.environ.get("OSS_ROOT_PREFIX", ""),
        help="日志根目录前缀(默认读 OSS_ROOT_PREFIX)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/metric_table_full.csv"),
        help="结果 CSV 输出路径",
    )
    parser.add_argument("--workers", type=int, default=8, help="并发取流线程数")
    args = parser.parse_args()

    if not args.endpoint or not args.bucket:
        raise SystemExit(
            "缺少 OSS 配置: 请通过 --endpoint/--bucket 或环境变量 OSS_ENDPOINT/OSS_BUCKET 提供"
        )

    rows = read_rows(Path(args.excel))
    if not rows:
        raise SystemExit(f"Excel 中未解析到有效行: {args.excel}")
    tasks = dedupe_tasks(rows)
    print(f"清单行: {len(rows)}, 去重后任务: {len(tasks)}", file=sys.stderr)

    bucket = make_oss_bucket(args.endpoint, args.bucket)
    summary = stream_compute_all(bucket, tasks, args.root_prefix, args.output, args.workers)
    if summary["ok"] + summary["skipped_done"] != summary["total"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
