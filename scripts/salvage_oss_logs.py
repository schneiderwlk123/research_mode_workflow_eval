"""按 uid + 日期 + 问题开头文本,从 OSS 快速打捞异常日志。

使用场景:用户反馈问题时报出手机号、大致日期与问题描述开头。手机号→uid 由
管理后台人工查询;本脚本拿 uid 直接列举 OSS `<root>/<uid>/` 下全部对象,
按文件名解析出 (msg_id, query 开头, UTC 时间戳) 后做两级过滤:

1. 日期过滤:文件名时间戳经实勘为 UTC(与日志内事件 epoch 一致),用户报的
   日期按北京时间理解,换算成 UTC 窗口 [前一天 16:00Z, 当天 16:00Z)。
2. query 过滤:归一化(去空白/标点/大小写)后两级匹配——先双向开头匹配
   (文件名 query 可能被截断,用户给的也可能只是开头片段),不中再单向包含
   匹配(定时任务文件名带"【定时任务执行】任务名称:"统一前缀,用户报的
   任务名在中间);命中后同 msg_id 的 noquery 版本一并带出(异常诊断需要
   全量版本)。

两级均未命中时回退返回当天全部候选并标记 date_only,供人工肉眼挑选,
避免"用户记忆措辞与实际 query 有出入"时直接空手而归。

默认只打印候选清单不落盘;--download 才下载,已存在且非空的文件跳过
(断点续传)。单 uid 列举失败隔离,不中断多 uid 批处理。

用法示例:
  python -m scripts.salvage_oss_logs --uid 3694996 --date 2026-09-10 --query "定时执行"
  python -m scripts.salvage_oss_logs --uid 3694996 --date 2026-09-10 --query "定时执行" --download
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.export_oss_jsonl import load_env_file, make_oss_bucket

# 北京时间:用户报的日期按此口径,文件名 UTC 时间戳 +8h 后比较
_BJ_TZ = timezone(timedelta(hours=8))

# 文件名尾部时间戳:2026-09-10T08-00-03-911Z(UTC,毫秒,连字符分隔)
_TS_RE = re.compile(
    r"_(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z\.jsonl$"
)
# 裸时间戳(不带 _ 前缀与 .jsonl 后缀),供日期比较独立使用
_TS_CORE_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z"
)
# msg_id 形如 msg_<hex>,自身含一个下划线,query 文本里也可能有下划线
_MSG_RE = re.compile(r"^(msg_[^_]+)_(.+)$", re.DOTALL)
# 截断重叠匹配的最小重叠字数,见 matches_query_contains
_MIN_OVERLAP = 4


@dataclass(frozen=True)
class LogFilename:
    """日志文件名解析结果。"""

    msg_id: str
    query_text: str
    ts_utc: datetime
    is_noquery: bool


@dataclass
class Candidate:
    """一条打捞候选(一个 OSS 对象)。"""

    uid: str
    session_id: str
    msg_id: str
    query_text: str
    ts_utc: str
    ts_bj: str
    key: str
    is_noquery: bool
    # matched_by 取值:query=开头命中; contains=包含命中; sibling=同msg带出;
    # date=按日期; date_only=query未命中回退
    matched_by: str
    status: str = "pending"  # pending/ok/skipped/error,仅 --download 时更新


def parse_log_filename(name: str) -> LogFilename | None:
    """解析日志文件名;不符合命名规律返回 None(调用方跳过,不中断)。"""
    ts_match = _TS_RE.search(name)
    if not ts_match:
        return None
    y, mo, d, h, mi, s, ms = (int(g) for g in ts_match.groups())
    ts_utc = datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=UTC)
    stem = name[: ts_match.start()]
    msg_match = _MSG_RE.match(stem)
    if not msg_match:
        return None
    msg_id, query_text = msg_match.groups()
    return LogFilename(
        msg_id=msg_id,
        query_text=query_text,
        ts_utc=ts_utc,
        is_noquery=query_text == "noquery",
    )


def matches_bj_date(ts_filename: str, date_bj: str) -> bool:
    """文件名 UTC 时间戳是否落在北京时间 date_bj(YYYY-MM-DD)当天。

    直接在完整文件名(或裸时间戳)上匹配,省去先 parse 再比较的开销。
    """
    m = _TS_CORE_RE.search(ts_filename)
    if not m:
        return False
    y, mo, d, h, mi, s, ms = (int(g) for g in m.groups())
    dt_bj = datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=UTC).astimezone(_BJ_TZ)
    return dt_bj.strftime("%Y-%m-%d") == date_bj


def _normalize(text: str) -> str:
    """归一化:去空白/标点/大小写差异,只保留文字,用于容错匹配。"""
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()


def matches_query_head(filename_query: str, user_query: str) -> bool:
    """query 开头模糊匹配:双向 startswith。

    文件名 query 可能被截断(用户给的更长),用户也可能只记得开头几个字
    (文件名更长),两个方向的开头包含都算命中;中间词出现不算(用户确认
    提供的是问题开头信息)。
    """
    fn_norm = _normalize(filename_query)
    user_norm = _normalize(user_query)
    if not fn_norm or not user_norm:
        return False
    return fn_norm.startswith(user_norm) or user_norm.startswith(fn_norm)


def matches_query_contains(filename_query: str, user_query: str) -> bool:
    """query 包含匹配(次级):归一化后"用户词 in 文件名"单向包含 + 截断重叠。

    定时任务的文件名带统一前缀(如"【定时任务执行】任务名称:xxx"),用户
    报的任务名在中间而非开头,开头匹配必然落空,用本层级兜住。只做单向
    包含:文件名通常远短于用户可能的长描述,反向包含会让短文件名(如"好的")
    命中任意长查询,产生大量误报。

    截断重叠:线上文件名会按长度截断(实测"…昨日全量群消"少尾字),用户词
    反而更长时包含匹配也落空;若文件名尾部与用户词开头重叠 >= 4 字
    (_MIN_OVERLAP),视为同一问题的截断变体。4 字阈值用于压低巧合误报,
    同时天然排除"好的"这类超短文件名(长度不足 4 不可能重叠达标)。
    """
    fn_norm = _normalize(filename_query)
    user_norm = _normalize(user_query)
    if not fn_norm or not user_norm:
        return False
    if user_norm in fn_norm:
        return True
    for k in range(min(len(fn_norm), len(user_norm)), _MIN_OVERLAP - 1, -1):
        if fn_norm.endswith(user_norm[:k]):
            return True
    return False


def list_all_keys(bucket: Any, prefix: str) -> list[str]:
    """分页列举 prefix 下全部对象 key(oss2 单页默认最多 100/1000 条)。"""
    keys: list[str] = []
    marker = ""
    while True:
        result = bucket.list_objects(prefix, marker=marker)
        keys.extend(obj.key for obj in result.object_list)
        if not result.is_truncated:
            return sorted(keys)
        marker = result.next_marker


def find_candidates(
    keys: list[str],
    root_prefix: str,
    uid: str,
    date_bj: str,
    query: str | None,
) -> list[Candidate]:
    """在已列举的 keys 上做日期 + query 两级过滤,返回候选(按北京时间排序)。"""
    prefix = f"{root_prefix.rstrip('/')}/{uid}/"
    on_date: list[tuple[LogFilename, str]] = []
    for key in keys:
        if not key.startswith(prefix):
            continue
        parsed = parse_log_filename(PurePosixPath(key).name)
        if parsed is None:
            continue
        if matches_bj_date(key, date_bj):
            on_date.append((parsed, key))

    def to_candidate(parsed: LogFilename, key: str, matched_by: str) -> Candidate:
        parts = PurePosixPath(key).parts
        # key = <root...>/<uid>/<session>/<filename>,session 取 uid 后一段
        session_id = parts[-2] if len(parts) >= 2 else ""
        return Candidate(
            uid=uid,
            session_id=session_id,
            msg_id=parsed.msg_id,
            query_text=parsed.query_text,
            ts_utc=parsed.ts_utc.isoformat(),
            ts_bj=parsed.ts_utc.astimezone(_BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            key=key,
            is_noquery=parsed.is_noquery,
            matched_by=matched_by,
        )

    if not query:
        candidates = [to_candidate(p, k, "date") for p, k in on_date]
    else:
        # 两级匹配:开头命中优先标记 query,仅包含命中标记 contains
        matched: list[tuple[LogFilename, str, str]] = []
        for p, k in on_date:
            if p.is_noquery:
                continue
            if matches_query_head(p.query_text, query):
                matched.append((p, k, "query"))
            elif matches_query_contains(p.query_text, query):
                matched.append((p, k, "contains"))
        if matched:
            hit_msg_ids = {p.msg_id for p, _, _ in matched}
            candidates = [to_candidate(p, k, mb) for p, k, mb in matched]
            # 同 msg_id 的 noquery/多时间戳版本一并带出,异常诊断要看全
            matched_keys = {(p.msg_id, k) for p, k, _ in matched}
            candidates += [
                to_candidate(p, k, "sibling")
                for p, k in on_date
                if p.msg_id in hit_msg_ids and (p.msg_id, k) not in matched_keys
            ]
        else:
            # 两级均未命中:回退当天全量,标记 date_only 供人工挑选
            candidates = [to_candidate(p, k, "date_only") for p, k in on_date]
    candidates.sort(key=lambda c: c.ts_utc)
    return candidates


def salvage(
    bucket: Any,
    root_prefix: str,
    uids: list[str],
    date_bj: str,
    query: str | None,
    output_dir: Path,
    download: bool = False,
) -> dict[str, Any]:
    """打捞主流程:列举 → 过滤 → (可选)下载 + manifest。单 uid 失败隔离。"""
    all_candidates: list[Candidate] = []
    errors: list[dict[str, str]] = []
    for uid in uids:
        try:
            keys = list_all_keys(bucket, f"{root_prefix.rstrip('/')}/{uid}/")
        except Exception as exc:  # noqa: BLE001 - 单 uid 失败隔离,不中断批处理
            errors.append({"uid": uid, "error": f"{type(exc).__name__}: {exc}"})
            continue
        all_candidates += find_candidates(keys, root_prefix, uid, date_bj, query)

    summary = {
        "uids": len(uids),
        "candidates": len(all_candidates),
        "downloaded": 0,
        "skipped": 0,
        "error": len(errors),
    }

    if download and all_candidates:
        output_dir.mkdir(parents=True, exist_ok=True)
        for cand in all_candidates:
            local = output_dir / cand.uid / cand.session_id / PurePosixPath(cand.key).name
            try:
                # 断点续传:已存在且非空即跳过,重复运行只补未完成的
                if local.is_file() and local.stat().st_size > 0:
                    cand.status = "skipped"
                    summary["skipped"] += 1
                    continue
                local.parent.mkdir(parents=True, exist_ok=True)
                bucket.get_object_to_file(cand.key, str(local))
                cand.status = "ok"
                summary["downloaded"] += 1
            except Exception as exc:  # noqa: BLE001 - 单文件失败隔离
                cand.status = "error"
                summary["error"] += 1
                errors.append(
                    {"uid": cand.uid, "key": cand.key, "error": f"{type(exc).__name__}: {exc}"}
                )
        manifest = {
            "root_prefix": root_prefix,
            "date_bj": date_bj,
            "query": query,
            "summary": summary,
            "errors": errors,
            "candidates": [asdict(c) for c in all_candidates],
        }
        (output_dir / "salvage_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return {"candidates": all_candidates, "summary": summary, "errors": errors}


def _print_candidates(candidates: list[Candidate]) -> None:
    """打印候选清单表格(北京时间/session/msg_id/问题开头/匹配方式)。"""
    if not candidates:
        print("当天无匹配日志。请确认:日期是否为北京时间口径、uid 是否正确、该用户当天是否活跃。")
        return
    print(f"{'北京时间':<20} {'session':<34} {'msg_id':<38} {'匹配':<10} 问题开头")
    for c in candidates:
        version = "noquery" if c.is_noquery else "query"
        head = c.query_text[:30].replace("\n", " ")
        tag = f"{c.matched_by}/{version}"
        print(f"{c.ts_bj:<20} {c.session_id:<34} {c.msg_id:<38} {tag:<16} {head}")


def main() -> None:
    load_env_file(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser(description="按 uid+日期+问题开头从 OSS 打捞异常日志")
    parser.add_argument("--uid", action="append", required=True, help="用户 uid,可重复传多个")
    parser.add_argument("--date", required=True, help="北京时间日期 YYYY-MM-DD")
    parser.add_argument("--query", default=None, help="问题开头文本(可选;缺省列当天全部)")
    parser.add_argument("--download", action="store_true", help="实际下载(缺省只打印候选清单)")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("OSS_ENDPOINT", ""),
        help="OSS endpoint(默认读 OSS_ENDPOINT)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("OSS_BUCKET", ""),
        help="OSS bucket(默认读 OSS_BUCKET)",
    )
    parser.add_argument(
        "--root-prefix",
        default=os.environ.get("OSS_ROOT_PREFIX", ""),
        help="日志根前缀(默认读 OSS_ROOT_PREFIX)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/oss_salvage"),
        help="下载输出目录(默认 data/oss_salvage)",
    )
    args = parser.parse_args()

    if not args.endpoint or not args.bucket:
        raise SystemExit(
            "缺少 OSS 配置: 请通过 --endpoint/--bucket 或环境变量 OSS_ENDPOINT/OSS_BUCKET 提供"
        )

    bucket = make_oss_bucket(args.endpoint, args.bucket)
    result = salvage(
        bucket, args.root_prefix, args.uid, args.date, args.query, args.output, args.download
    )
    _print_candidates(result["candidates"])
    for err in result["errors"]:
        print(f"[错误] {err}", file=sys.stderr)
    print(f"摘要: {result['summary']}")
    if result["candidates"] and not args.download:
        print("提示: 确认候选后追加 --download 实际下载。")
    if result["summary"]["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
