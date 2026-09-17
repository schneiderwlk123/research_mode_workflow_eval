"""salvage_oss_logs 的行为测试:文件名解析、北京时间日期过滤、query 开头模糊匹配、下载与断点续传。

FakeBucket 模拟 oss2 的分页 list_objects(marker 机制)与 get_object_to_file。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.salvage_oss_logs import (
    find_candidates,
    list_all_keys,
    matches_bj_date,
    matches_query_contains,
    matches_query_head,
    parse_log_filename,
    salvage,
)

ROOT = "opencode_logs/prod"


def make_key(uid: str, session: str, filename: str) -> str:
    return f"{ROOT}/{uid}/{session}/{filename}"


class FakeBucket:
    """按 marker 分页的假 bucket;每页 page_size 个,记录下载调用。"""

    def __init__(self, keys: list[str], page_size: int = 2) -> None:
        self.keys = sorted(keys)
        self.page_size = page_size
        self.downloaded: list[str] = []

    def list_objects(self, prefix: str, marker: str = "") -> Any:
        matched = [k for k in self.keys if k.startswith(prefix) and k > marker]
        page = matched[: self.page_size]
        return type(
            "ListResult",
            (),
            {
                "object_list": [type("Obj", (), {"key": k})() for k in page],
                "is_truncated": len(matched) > len(page),
                "next_marker": page[-1] if page else marker,
            },
        )()

    def get_object_to_file(self, key: str, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"key": key}), encoding="utf-8")
        self.downloaded.append(key)


class TestParseLogFilename:
    def test_解析带query文件名(self) -> None:
        parsed = parse_log_filename(
            "msg_fcac15cc_定时执行。 每天九点报时_2026-09-10T08-00-03-911Z.jsonl"
        )
        assert parsed is not None
        assert parsed.msg_id == "msg_fcac15cc"
        assert parsed.query_text == "定时执行。 每天九点报时"
        assert parsed.ts_utc.isoformat().startswith("2026-09-10T08:00:03")
        assert parsed.is_noquery is False

    def test_解析noquery版本(self) -> None:
        parsed = parse_log_filename("msg_ab12_noquery_2026-09-10T08-00-03-911Z.jsonl")
        assert parsed is not None
        assert parsed.is_noquery is True
        assert parsed.query_text == "noquery"

    def test_非法文件名返回None(self) -> None:
        assert parse_log_filename("manifest.json") is None
        assert parse_log_filename("msg_x_没有时序后缀.jsonl") is None


class TestMatchesBjDate:
    def test_北京时间当天命中(self) -> None:
        # 2026-09-10T14:00Z = 北京时间 2026-09-10 22:00
        assert matches_bj_date("2026-09-10T14-00-20-017Z", "2026-09-10") is True

    def test_UTC同日但北京已跨日不命中(self) -> None:
        # 2026-09-10T19:00Z = 北京时间 2026-09-11 03:00 → 属于北京的 9/11
        assert matches_bj_date("2026-09-10T19-00-17-761Z", "2026-09-10") is False
        assert matches_bj_date("2026-09-10T19-00-17-761Z", "2026-09-11") is True

    def test_北京零点的UTC边界(self) -> None:
        # 16:00Z 整 = 北京时间次日 00:00 → 属于次日
        assert matches_bj_date("2026-09-10T16-00-00-000Z", "2026-09-11") is True
        assert matches_bj_date("2026-09-10T15-59-59-999Z", "2026-09-10") is True


class TestMatchesQueryHead:
    def test_开头包含命中(self) -> None:
        assert matches_query_head("定时执行。 每天九点报时", "定时执行") is True

    def test_文件名被截断时反向开头命中(self) -> None:
        # 文件名只保留了 query 开头一段,用户给的比文件名还长
        assert matches_query_head("定时执行", "定时执行。 每天九点报时") is True

    def test_归一化空白与标点(self) -> None:
        assert matches_query_head("定时执行。每天九点,报时", "定时执行 每天九点 报时") is True

    def test_中间词不算开头匹配(self) -> None:
        assert matches_query_head("帮我分析定时执行的策略", "定时执行") is False


class TestMatchesQueryContains:
    def test_中间包含命中(self) -> None:
        # 定时任务文件名带统一前缀,用户报的任务名在中间
        assert matches_query_contains(
            "【定时任务执行】 任务名称：昨日全量群消息汇总", "昨日全量群消息"
        ) is True

    def test_归一化标点空白(self) -> None:
        assert matches_query_contains("任务名称:昨日全量群消息", "昨日 全量 群消息") is True

    def test_不包含不命中(self) -> None:
        assert (
            matches_query_contains("【定时任务执行】 任务名称：五标的准实时", "昨日全量群消息")
            is False
        )

    def test_反向不做包含防短文件名过度匹配(self) -> None:
        # 只做"用户词 in 文件名"单向包含,避免短文件名命中任意长查询
        assert matches_query_contains("好的", "好的,请帮我分析昨日全量群消息") is False

    def test_文件名被截断时重叠匹配(self) -> None:
        # 线上文件名按长度截断:"…昨日全量群消"少了"息"字,
        # 文件名尾部与用户词开头重叠 >=4 字即命中
        assert matches_query_contains(
            "【定时任务执行】 任务名称：昨日全量群消", "昨日全量群消息"
        ) is True

    def test_重叠不足4字不命中(self) -> None:
        # 仅 2 字重叠属巧合概率高,不放行
        assert matches_query_contains("任务名称：五标的准实时", "实时行情分析") is False


class TestListAllKeys:
    def test_跨页取全(self) -> None:
        keys = [
            make_key("u1", f"ses_{i}", f"msg_{i}_q_2026-09-10T0{i}-00-00-000Z.jsonl")
            for i in range(5)
        ]
        bucket = FakeBucket(keys, page_size=2)
        assert list_all_keys(bucket, f"{ROOT}/u1/") == sorted(keys)


class TestFindCandidates:
    KEYS = [
        make_key("u1", "ses_a", "msg_1_定时执行 每天报时_2026-09-10T14-00-20-017Z.jsonl"),
        make_key("u1", "ses_a", "msg_1_noquery_2026-09-10T14-00-20-017Z.jsonl"),
        make_key("u1", "ses_b", "msg_2_别的任务_2026-09-10T02-00-00-000Z.jsonl"),
        make_key("u1", "ses_c", "msg_3_定时执行_2026-09-09T01-00-00-000Z.jsonl"),
    ]

    def test_按日期与query过滤并带出同msg的noquery版本(self) -> None:
        cands = find_candidates(self.KEYS, ROOT, "u1", "2026-09-10", "定时执行")
        msgs = {c.msg_id for c in cands if not c.is_noquery}
        assert msgs == {"msg_1"}
        # noquery 同名 msg 版本一并带出
        assert any(c.is_noquery and c.msg_id == "msg_1" for c in cands)
        # 9-09 的(北京时间也是 9-09 09:00)不在当天
        assert all(c.msg_id != "msg_3" for c in cands)

    def test_query未命中时回退当天全量并标记fallback(self) -> None:
        cands = find_candidates(self.KEYS, ROOT, "u1", "2026-09-10", "不存在的词")
        assert cands, "应回退返回当天全部候选"
        assert all(c.matched_by == "date_only" for c in cands)

    def test_不给query时按日期全量(self) -> None:
        cands = find_candidates(self.KEYS, ROOT, "u1", "2026-09-10", None)
        assert {c.msg_id for c in cands} == {"msg_1", "msg_2"}

    def test_开头不中时包含匹配次级命中(self) -> None:
        # 定时任务前缀导致"每日纪要"不在开头,应 contains 命中且只返回相关候选
        keys = self.KEYS + [
            make_key(
                "u1", "ses_d",
                "msg_4_【定时任务执行】 任务名称：每日纪要汇总_2026-09-10T01-30-00-000Z.jsonl",
            ),
        ]
        cands = find_candidates(keys, ROOT, "u1", "2026-09-10", "每日纪要")
        assert [(c.msg_id, c.matched_by) for c in cands] == [("msg_4", "contains")]

    def test_开头匹配优先于包含匹配(self) -> None:
        keys = self.KEYS + [
            make_key("u1", "ses_d", "msg_5_前缀定时执行后缀_2026-09-10T01-30-00-000Z.jsonl"),
        ]
        cands = find_candidates(keys, ROOT, "u1", "2026-09-10", "定时执行")
        matched = {(c.msg_id, c.matched_by) for c in cands}
        assert ("msg_1", "query") in matched       # 开头命中
        assert ("msg_5", "contains") in matched    # 仅包含命中
        assert ("msg_1", "sibling") in matched     # noquery 版本带出
        assert ("msg_2", "date_only") not in matched  # 无关候选不混入

    def test_包含也未命中才回退全量(self) -> None:
        cands = find_candidates(self.KEYS, ROOT, "u1", "2026-09-10", "完全不存在的词")
        assert cands and all(c.matched_by == "date_only" for c in cands)


class TestSalvage:
    def test_默认只列清单不落盘(self, tmp_path: Path) -> None:
        keys = [make_key("u1", "ses_a", "msg_1_定时执行_2026-09-10T14-00-20-017Z.jsonl")]
        bucket = FakeBucket(keys)
        result = salvage(bucket, ROOT, ["u1"], "2026-09-10", "定时执行", tmp_path, download=False)
        assert len(result["candidates"]) == 1
        assert bucket.downloaded == []
        assert not (tmp_path / "salvage_manifest.json").exists()

    def test_下载并写manifest且断点续传跳过已有文件(self, tmp_path: Path) -> None:
        keys = [make_key("u1", "ses_a", "msg_1_定时执行_2026-09-10T14-00-20-017Z.jsonl")]
        bucket = FakeBucket(keys)
        r1 = salvage(bucket, ROOT, ["u1"], "2026-09-10", "定时执行", tmp_path, download=True)
        assert r1["summary"]["downloaded"] == 1
        target = tmp_path / "u1" / "ses_a" / Path(keys[0]).name
        assert target.is_file()
        manifest = json.loads((tmp_path / "salvage_manifest.json").read_text(encoding="utf-8"))
        assert manifest["candidates"][0]["status"] == "ok"

        # 第二次运行:文件已存在且非空 → 跳过下载
        bucket2 = FakeBucket(keys)
        r2 = salvage(bucket2, ROOT, ["u1"], "2026-09-10", "定时执行", tmp_path, download=True)
        assert r2["summary"]["skipped"] == 1
        assert bucket2.downloaded == []

    def test_单uid列举失败隔离不中断(self, tmp_path: Path) -> None:
        class BrokenBucket(FakeBucket):
            def list_objects(self, prefix: str, marker: str = "") -> Any:
                if "u_bad" in prefix and not marker:
                    raise RuntimeError("boom")
                return super().list_objects(prefix, marker)

        keys = [make_key("u_ok", "ses_a", "msg_1_定时执行_2026-09-10T14-00-20-017Z.jsonl")]
        bad_key = make_key("u_bad", "s", "msg_9_x_2026-09-10T14-00-20-017Z.jsonl")
        bucket = BrokenBucket(keys)
        bucket.keys = sorted(keys + [bad_key])
        result = salvage(
            bucket, ROOT, ["u_bad", "u_ok"], "2026-09-10", None, tmp_path, download=False
        )
        assert result["summary"]["error"] == 1
        assert any(c.uid == "u_ok" for c in result["candidates"])
