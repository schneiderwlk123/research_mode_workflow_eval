# 测试 scripts/stream_compute_metrics.py 的纯逻辑与流式管线:
# 任务去重、带 query 版本选择、流式解析计算、增量写 CSV、断点续跑和失败隔离。
# 使用假 bucket(内存 BytesIO 流)隔离真实 OSS,全程不写日志文件到磁盘。
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.stream_compute_metrics import (
    dedupe_tasks,
    select_query_version,
    stream_compute_all,
)


def _research_events(
    message_id: str = "msg_a1", *, with_turn_end: bool = True
) -> list[dict[str, Any]]:
    """构造最小研究模式事件流:身份可识别,指标可算出 NOT_COMPUTABLE 以外的稳定结果。"""
    events: list[dict[str, Any]] = [
        {"type": "turn.start", "userMessageID": message_id, "uid": "u1", "sessionID": "s1"},
        {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "tool",
                    "callID": "c1",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {}, "time": {"start": 0, "end": 1}},
                }
            },
        },
    ]
    if with_turn_end:
        events.append({"type": "turn.end", "durationMs": 1500})
    return events


def _jsonl_bytes(events: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n").encode("utf-8")


class FakeStreamBucket:
    """模拟 oss2.Bucket 流式接口:list_objects 返回 key 列表,get_object 返回内存流。"""

    def __init__(self, objects: dict[str, bytes], fail_keys: set[str] | None = None) -> None:
        self._objects = objects
        self._fail_keys = fail_keys or set()
        self.fetched: list[str] = []

    def list_objects(self, prefix: str) -> Any:
        class _Result:
            object_list = [
                type("Obj", (), {"key": k})() for k in self._objects if k.startswith(prefix)
            ]

        return _Result()

    def get_object(self, key: str) -> io.BytesIO:
        if key in self._fail_keys:
            raise OSError("模拟流式读取失败")
        self.fetched.append(key)
        return io.BytesIO(self._objects[key])


class TestDedupeTasks:
    def test_dedupes_preserving_order(self) -> None:
        rows = [("u1", "s1", "m1"), ("u2", "s2", "m2"), ("u1", "s1", "m1")]
        assert dedupe_tasks(rows) == [("u1", "s1", "m1"), ("u2", "s2", "m2")]


class TestSelectQueryVersion:
    def test_prefers_query_version(self) -> None:
        keys = [
            "r/u/s/m1_noquery_2026-09-11T12-35-29-546Z.jsonl",
            "r/u/s/m1_今天天气_2026-09-11T12-00-18-718Z.jsonl",
        ]
        assert select_query_version(keys) == "r/u/s/m1_今天天气_2026-09-11T12-00-18-718Z.jsonl"

    def test_multiple_query_versions_take_latest(self) -> None:
        keys = [
            "r/u/s/m1_查询A_2026-09-11T09-00-00-000Z.jsonl",
            "r/u/s/m1_查询B_2026-09-13T10-00-00-000Z.jsonl",
            "r/u/s/m1_查询C_2026-09-12T10-00-00-000Z.jsonl",
        ]
        assert select_query_version(keys) == "r/u/s/m1_查询B_2026-09-13T10-00-00-000Z.jsonl"

    def test_only_noquery_returns_none(self) -> None:
        # 用户口径:只用带 query 版;仅有 noquery 时不猜测,返回 None 由调用方记录跳过
        keys = ["r/u/s/m1_noquery_2026-09-11T09-00-00-000Z.jsonl"]
        assert select_query_version(keys) is None

    def test_empty_returns_none(self) -> None:
        assert select_query_version([]) is None


class TestStreamComputeAll:
    def _objects(self) -> dict[str, bytes]:
        return {
            "r/u1/s1/m1_查询_2026-09-11T09-00-00-000Z.jsonl": _jsonl_bytes(
                _research_events("m1")
            ),
            "r/u2/s2/m2_查询_2026-09-11T09-00-00-000Z.jsonl": _jsonl_bytes(
                _research_events("m2")
            ),
        }

    def test_writes_csv_rows_incrementally(self, tmp_path: Path) -> None:
        bucket = FakeStreamBucket(self._objects())
        out = tmp_path / "metric_table.csv"
        summary = stream_compute_all(
            bucket, [("u1", "s1", "m1"), ("u2", "s2", "m2")], "r", out, workers=2
        )
        assert summary["ok"] == 2
        with out.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert {r["log_id"] for r in rows} == {"m1", "m2"}
        assert all(r["mode"] == "research" for r in rows)

    def test_no_local_log_files_written(self, tmp_path: Path) -> None:
        # 核心约束:除结果 CSV 与诊断 JSONL 外不得有任何日志文件落盘
        bucket = FakeStreamBucket(self._objects())
        out = tmp_path / "metric_table.csv"
        stream_compute_all(bucket, [("u1", "s1", "m1")], "r", out, workers=1)
        written = {p.name for p in tmp_path.rglob("*") if p.is_file()}
        assert written == {"metric_table.csv", "issues.jsonl"}

    def test_resume_skips_completed_log_ids(self, tmp_path: Path) -> None:
        bucket = FakeStreamBucket(self._objects())
        out = tmp_path / "metric_table.csv"
        stream_compute_all(bucket, [("u1", "s1", "m1")], "r", out, workers=1)
        bucket2 = FakeStreamBucket(self._objects())
        summary = stream_compute_all(
            bucket2, [("u1", "s1", "m1"), ("u2", "s2", "m2")], "r", out, workers=1
        )
        assert bucket2.fetched == ["r/u2/s2/m2_查询_2026-09-11T09-00-00-000Z.jsonl"]
        assert summary["skipped_done"] == 1
        assert summary["ok"] == 1
        with out.open(encoding="utf-8", newline="") as f:
            assert len(list(csv.DictReader(f))) == 2  # 不重复写 m1

    def test_fetch_failure_isolated_as_issue(self, tmp_path: Path) -> None:
        # 部分失败:单条流读取失败记入 issues.jsonl,不影响其余任务
        key = "r/u1/s1/m1_查询_2026-09-11T09-00-00-000Z.jsonl"
        bucket = FakeStreamBucket(self._objects(), fail_keys={key})
        out = tmp_path / "metric_table.csv"
        summary = stream_compute_all(
            bucket, [("u1", "s1", "m1"), ("u2", "s2", "m2")], "r", out, workers=1
        )
        assert summary["ok"] == 1
        assert summary["error"] == 1
        issues = [
            json.loads(line)
            for line in (tmp_path / "issues.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(i.get("kind") == "error" and i["msg_id"] == "m1" for i in issues)

    def test_missing_and_no_query_version_recorded(self, tmp_path: Path) -> None:
        objects = {"r/u3/s3/m3_noquery_2026-09-11T09-00-00-000Z.jsonl": b"{}"}
        bucket = FakeStreamBucket(objects)
        out = tmp_path / "metric_table.csv"
        summary = stream_compute_all(
            bucket, [("u3", "s3", "m3"), ("u4", "s4", "m4")], "r", out, workers=1
        )
        assert summary["no_query_version"] == 1
        assert summary["missing"] == 1
        assert summary["ok"] == 0
        assert bucket.fetched == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
