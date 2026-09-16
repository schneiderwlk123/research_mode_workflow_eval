# 测试 scripts/export_oss_jsonl.py 的纯逻辑部分:
# Excel 行解析、OSS 前缀拼接、单条导出状态分类与 manifest 汇总。
# 使用假 bucket 隔离真实 OSS 边界,保证测试可离线复现。
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from scripts.export_oss_jsonl import (
    build_prefix,
    export_one,
    load_env_file,
    read_rows,
    run_export,
)


class TestLoadEnvFile:
    def test_sets_missing_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OSS_BUCKET", raising=False)
        env = tmp_path / ".env"
        env.write_text("OSS_BUCKET=my-bucket\n# 注释行\n\nOSS_ENDPOINT=oss-cn.aliyuncs.com\n",
                       encoding="utf-8")
        load_env_file(env)
        assert os.environ["OSS_BUCKET"] == "my-bucket"
        assert os.environ["OSS_ENDPOINT"] == "oss-cn.aliyuncs.com"
        monkeypatch.delenv("OSS_BUCKET")
        monkeypatch.delenv("OSS_ENDPOINT")

    def test_process_env_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # 进程环境变量优先于文件值,保证临时覆盖不需要改 .env
        monkeypatch.setenv("OSS_BUCKET", "from-process")
        env = tmp_path / ".env"
        env.write_text("OSS_BUCKET=from-file\n", encoding="utf-8")
        load_env_file(env)
        assert os.environ["OSS_BUCKET"] == "from-process"

    def test_missing_file_is_noop(self, tmp_path: Path) -> None:
        # .env 不存在不应报错,配置仍可全部走进程环境变量
        load_env_file(tmp_path / "absent.env")

    def test_strips_quotes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OSS_ROOT_PREFIX", raising=False)
        env = tmp_path / ".env"
        env.write_text('OSS_ROOT_PREFIX="agent logs"\n', encoding="utf-8")
        load_env_file(env)
        assert os.environ["OSS_ROOT_PREFIX"] == "agent logs"
        monkeypatch.delenv("OSS_ROOT_PREFIX")


class FakeBucket:
    """模拟 oss2.Bucket 的最小行为:list_objects 按前缀返回 key,下载写本地文件。"""

    def __init__(self, keys: list[str], fail_keys: set[str] | None = None) -> None:
        self._keys = keys
        self._fail_keys = fail_keys or set()
        self.downloaded: list[tuple[str, str]] = []

    def list_objects(self, prefix: str) -> Any:
        class _Result:
            object_list = [
                type("Obj", (), {"key": k})() for k in self._keys if k.startswith(prefix)
            ]

        return _Result()

    def get_object_to_file(self, key: str, dest: str) -> None:
        if key in self._fail_keys:
            raise OSError("模拟下载失败")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(json.dumps({"key": key}), encoding="utf-8")
        self.downloaded.append((key, dest))


def _make_xlsx(path: Path, rows: list[tuple[Any, Any, Any]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(("uid", "session_id", "msg_id", "total_tokens", "chat_mode", "create_time"))
    for r in rows:
        ws.append((*r, "100", "2", "2026-09-10 00:00:00"))
    wb.save(path)


class TestReadRows:
    def test_parses_uid_session_msg(self, tmp_path: Path) -> None:
        p = tmp_path / "a.xlsx"
        _make_xlsx(p, [("u1", "s1", "m1"), ("u2", "s2", "m2")])
        assert read_rows(p) == [("u1", "s1", "m1"), ("u2", "s2", "m2")]

    def test_skips_incomplete_rows(self, tmp_path: Path) -> None:
        # 边界:缺失 msg_id 或全空行不应产出任务,避免拼出错误前缀
        p = tmp_path / "a.xlsx"
        _make_xlsx(p, [("u1", "s1", None), ("", "", "")])
        assert read_rows(p) == []

    def test_numeric_uid_normalized_to_str(self, tmp_path: Path) -> None:
        # Excel 中 uid 可能被识别为数字,需统一为无小数点的字符串
        p = tmp_path / "a.xlsx"
        _make_xlsx(p, [(3694996, "s1", "m1")])
        assert read_rows(p) == [("3694996", "s1", "m1")]


class TestBuildPrefix:
    def test_joins_with_slashes(self) -> None:
        assert build_prefix("logs", "u1", "s1", "m1") == "logs/u1/s1/m1"

    def test_strips_extra_slashes(self) -> None:
        # 根前缀带尾部斜杠时不应产生双斜杠
        assert build_prefix("logs/", "u1", "s1", "m1") == "logs/u1/s1/m1"

    def test_empty_root(self) -> None:
        assert build_prefix("", "u1", "s1", "m1") == "u1/s1/m1"


class TestExportOne:
    def test_single_match_downloads(self, tmp_path: Path) -> None:
        bucket = FakeBucket(["logs/u1/s1/m1_a.jsonl", "logs/u1/s1/other.jsonl"])
        rec = export_one(bucket, ("u1", "s1", "m1"), "logs", tmp_path)
        assert rec["status"] == "ok"
        assert rec["keys"] == ["logs/u1/s1/m1_a.jsonl"]
        assert Path(rec["local_paths"][0]).exists()

    def test_no_match_marks_missing(self, tmp_path: Path) -> None:
        bucket = FakeBucket(["logs/u9/s9/m9.jsonl"])
        rec = export_one(bucket, ("u1", "s1", "m1"), "logs", tmp_path)
        assert rec["status"] == "missing"
        assert bucket.downloaded == []

    def test_multiple_matches_downloads_all(self, tmp_path: Path) -> None:
        # 同一 msg_id 前缀可能同时存在 noquery 版与带 query 版,约定全部下载不猜测取舍
        bucket = FakeBucket(["logs/u1/s1/m1_noquery_x.jsonl", "logs/u1/s1/m1_query_y.jsonl"])
        rec = export_one(bucket, ("u1", "s1", "m1"), "logs", tmp_path)
        assert rec["status"] == "ok"
        assert rec["keys"] == ["logs/u1/s1/m1_noquery_x.jsonl", "logs/u1/s1/m1_query_y.jsonl"]
        assert len(rec["local_paths"]) == 2
        assert all(Path(p).exists() for p in rec["local_paths"])

    def test_partial_download_failure_marks_error(self, tmp_path: Path) -> None:
        # 部分失败:多文件中一个下载失败记为 error,但成功文件保留,重跑只补失败项
        bucket = FakeBucket(
            ["logs/u1/s1/m1_a.jsonl", "logs/u1/s1/m1_b.jsonl"],
            fail_keys={"logs/u1/s1/m1_b.jsonl"},
        )
        rec = export_one(bucket, ("u1", "s1", "m1"), "logs", tmp_path)
        assert rec["status"] == "error"
        assert "模拟下载失败" in rec["error"]
        assert len(rec["local_paths"]) == 1  # 成功的文件仍已落盘

    def test_download_error_isolated(self, tmp_path: Path) -> None:
        # 部分失败:单条下载异常不应中断整批,记录为 error
        bucket = FakeBucket(["logs/u1/s1/m1.jsonl"], fail_keys={"logs/u1/s1/m1.jsonl"})
        rec = export_one(bucket, ("u1", "s1", "m1"), "logs", tmp_path)
        assert rec["status"] == "error"
        assert "模拟下载失败" in rec["error"]

    def test_local_layout_preserves_uid_session(self, tmp_path: Path) -> None:
        bucket = FakeBucket(["logs/u1/s1/m1_x.jsonl"])
        rec = export_one(bucket, ("u1", "s1", "m1"), "logs", tmp_path)
        rel = Path(rec["local_paths"][0]).relative_to(tmp_path)
        assert rel.parts[0] == "u1" and rel.parts[1] == "s1"


class TestRunExport:
    def test_manifest_summary(self, tmp_path: Path) -> None:
        bucket = FakeBucket(
            ["logs/u1/s1/m1.jsonl", "logs/u2/s2/m2.jsonl"],
        )
        rows = [("u1", "s1", "m1"), ("u2", "s2", "m2"), ("u3", "s3", "m3")]
        manifest = run_export(bucket, rows, "logs", tmp_path, workers=2)
        summary = manifest["summary"]
        assert summary["total"] == 3
        assert summary["ok"] == 2
        assert summary["missing"] == 1
        assert len(manifest["records"]) == 3

    def test_duplicate_rows_deduped(self, tmp_path: Path) -> None:
        # Excel 存在重复行,同一 (uid, session_id, msg_id) 只应产生一条任务
        bucket = FakeBucket(["logs/u1/s1/m1.jsonl"])
        rows = [("u1", "s1", "m1"), ("u1", "s1", "m1")]
        manifest = run_export(bucket, rows, "logs", tmp_path, workers=1)
        assert manifest["summary"]["total"] == 1
        assert len(bucket.downloaded) == 1

    def test_manifest_written_to_disk(self, tmp_path: Path) -> None:
        bucket = FakeBucket(["logs/u1/s1/m1.jsonl"])
        run_export(bucket, [("u1", "s1", "m1")], "logs", tmp_path, workers=1)
        mf = tmp_path / "manifest.json"
        assert mf.exists()
        data = json.loads(mf.read_text(encoding="utf-8"))
        assert data["summary"]["ok"] == 1

    def test_resume_skips_existing_files(self, tmp_path: Path) -> None:
        # 重跑时已下载成功的条目不应重复下载,支持失败后续跑
        bucket = FakeBucket(["logs/u1/s1/m1.jsonl"])
        run_export(bucket, [("u1", "s1", "m1")], "logs", tmp_path, workers=1)
        first_count = len(bucket.downloaded)
        bucket2 = FakeBucket(["logs/u1/s1/m1.jsonl"])
        manifest = run_export(bucket2, [("u1", "s1", "m1")], "logs", tmp_path, workers=1)
        assert bucket2.downloaded == []
        assert manifest["records"][0]["status"] == "ok"
        assert first_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
