"""批量从 OSS 导出研究模式 trace 的 jsonl 日志。

输入:Excel 清单(uid / session_id / msg_id 三列,其余列忽略)。
OSS 存储约定:<根前缀>/<uid>/<session_id>/<msg_id 前缀>*.jsonl,每个 msg_id 前缀唯一对应一个文件。
输出:jsonl 文件下载到本地目录(默认 data/oss_jsonl/<uid>/<session_id>/<原文件名>),
并在同目录写 manifest.json 记录每条任务的状态(ok / missing / ambiguous / error / skipped),
失败或缺失不中断整批,支持直接重跑续传(已成功文件不重复下载)。

连接配置全部来自 CLI 参数或环境变量,严禁把 AK/SK 写入代码库:
  OSS_ENDPOINT / OSS_BUCKET / OSS_ROOT_PREFIX / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET

用法示例:
  python scripts/export_oss_jsonl.py \
      --excel 研究模式0909-0916.xlsx \
      --endpoint oss-cn-hangzhou.aliyuncs.com --bucket my-bucket --root-prefix agent_logs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# 纯逻辑部分(可离线测试,不依赖 oss2)
# ---------------------------------------------------------------------------


class BucketLike(Protocol):
    """oss2.Bucket 的最小协议,便于测试注入假对象。"""

    def list_objects(self, prefix: str) -> Any: ...

    def get_object_to_file(self, key: str, dest: str) -> None: ...


def load_env_file(path: Path) -> None:
    """把 .env 文件中的 KEY=VALUE 载入环境变量;进程已有的变量优先,文件不补充覆盖。

    文件不存在时静默跳过(配置可全部走进程环境变量);格式非法的行跳过而不是报错,
    避免注释或空行阻断导出。
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


def _cell_to_str(value: Any) -> str:
    """把 Excel 单元格值规范化为字符串;数字型 uid 去掉小数点(3694996.0 -> '3694996')。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_rows(xlsx_path: Path) -> list[tuple[str, str, str]]:
    """读取 Excel,返回 (uid, session_id, msg_id) 列表;三个字段任一为空的行直接跳过。"""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, read_only=True)
    ws = wb.worksheets[0]
    rows: list[tuple[str, str, str]] = []
    first = True
    for raw in ws.iter_rows(values_only=True):
        if first:  # 跳过表头
            first = False
            continue
        uid, session_id, msg_id = (_cell_to_str(v) for v in raw[:3])
        if uid and session_id and msg_id:
            rows.append((uid, session_id, msg_id))
    wb.close()
    return rows


def build_prefix(root_prefix: str, uid: str, session_id: str, msg_id: str) -> str:
    """按存储约定拼 OSS list 前缀:<root>/<uid>/<session_id>/<msg_id>,避免双斜杠。"""
    parts = [p for p in (root_prefix.strip("/"), uid, session_id, msg_id) if p]
    return "/".join(parts)


def export_one(
    bucket: BucketLike,
    row: tuple[str, str, str],
    root_prefix: str,
    dest_root: Path,
) -> dict[str, Any]:
    """导出单条任务的 jsonl,返回 manifest 记录。任何异常都被隔离为 error 状态,不向上抛出。"""
    uid, session_id, msg_id = row
    prefix = build_prefix(root_prefix, uid, session_id, msg_id)
    record: dict[str, Any] = {"uid": uid, "session_id": session_id, "msg_id": msg_id}
    try:
        keys = [o.key for o in bucket.list_objects(prefix).object_list]
    except Exception as exc:  # noqa: BLE001 - list 失败同样隔离,保证整批继续
        record.update(status="error", error=f"list 失败: {exc}")
        return record

    if not keys:
        record.update(status="missing", error=f"OSS 无匹配对象, prefix={prefix}")
        return record
    if len(keys) > 1:
        # 约定每个 msg_id 前缀只有一个文件;多个匹配说明数据或约定异常,不猜测下载哪个
        record.update(status="ambiguous", candidates=keys)
        return record

    key = keys[0]
    local_path = dest_root / uid / session_id / Path(key).name
    if local_path.exists() and local_path.stat().st_size > 0:
        # 断点续跑:已存在的文件视为成功,不重复下载
        record.update(status="ok", key=key, local_path=str(local_path), resumed=True)
        return record
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        bucket.get_object_to_file(key, str(local_path))
    except Exception as exc:  # noqa: BLE001 - 下载失败隔离为 error
        record.update(status="error", key=key, error=f"下载失败: {exc}")
        return record
    record.update(status="ok", key=key, local_path=str(local_path))
    return record


def run_export(
    bucket: BucketLike,
    rows: list[tuple[str, str, str]],
    root_prefix: str,
    dest_root: Path,
    workers: int = 8,
) -> dict[str, Any]:
    """并发导出全部任务并写 manifest.json,返回 manifest 字典。"""
    dest_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(export_one, bucket, row, root_prefix, dest_root): row for row in rows
        }
        done = 0
        for fut in as_completed(futures):
            records.append(fut.result())
            done += 1
            if done % 20 == 0 or done == len(rows):
                print(f"进度: {done}/{len(rows)}", file=sys.stderr)

    summary: dict[str, int] = {"total": len(records)}
    for rec in records:
        summary[rec["status"]] = summary.get(rec["status"], 0) + 1
    manifest = {"summary": summary, "records": records}
    manifest_path = dest_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"完成: {summary} , manifest: {manifest_path}", file=sys.stderr)
    return manifest


# ---------------------------------------------------------------------------
# 真实 OSS 边界(仅 main 使用,测试不触达)
# ---------------------------------------------------------------------------


def make_oss_bucket(endpoint: str, bucket_name: str) -> Any:
    """用环境变量中的 AK/SK 创建 oss2.Bucket;缺失凭证时给出可定位的报错。"""
    import oss2

    access_key_id = os.environ.get("OSS_ACCESS_KEY_ID", "")
    access_key_secret = os.environ.get("OSS_ACCESS_KEY_SECRET", "")
    if not access_key_id or not access_key_secret:
        raise SystemExit(
            "缺少凭证: 请设置环境变量 OSS_ACCESS_KEY_ID 与 OSS_ACCESS_KEY_SECRET"
        )
    auth = oss2.Auth(access_key_id, access_key_secret)
    return oss2.Bucket(auth, endpoint, bucket_name)


def main() -> None:
    # 先加载项目根目录 .env(相对脚本位置定位,与运行时 cwd 无关),再解析 CLI;
    # CLI 参数默认值读取环境变量,因此 .env 中的 OSS_* 配置自动生效。
    load_env_file(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser(description="按 Excel 清单批量导出 OSS jsonl 日志")
    parser.add_argument("--excel", required=True, help="包含 uid/session_id/msg_id 的 xlsx 路径")
    parser.add_argument(
        "--endpoint", default=os.environ.get("OSS_ENDPOINT", ""), help="OSS endpoint"
    )
    parser.add_argument(
        "--bucket", default=os.environ.get("OSS_BUCKET", ""), help="OSS bucket 名"
    )
    parser.add_argument(
        "--root-prefix",
        default=os.environ.get("OSS_ROOT_PREFIX", ""),
        help="日志根目录前缀(uid 文件夹的上一级),可为空",
    )
    parser.add_argument(
        "--dest", default="data/oss_jsonl", help="本地导出目录(默认 data/oss_jsonl)"
    )
    parser.add_argument("--workers", type=int, default=8, help="并发下载线程数")
    args = parser.parse_args()

    if not args.endpoint or not args.bucket:
        raise SystemExit(
            "缺少 OSS 配置: 请通过 --endpoint/--bucket 或环境变量 "
            "OSS_ENDPOINT/OSS_BUCKET 提供"
        )

    rows = read_rows(Path(args.excel))
    if not rows:
        raise SystemExit(f"Excel 中未解析到有效行: {args.excel}")
    print(f"解析到 {len(rows)} 条任务", file=sys.stderr)

    bucket = make_oss_bucket(args.endpoint, args.bucket)
    manifest = run_export(bucket, rows, args.root_prefix, Path(args.dest), args.workers)
    # 存在缺失/异常时以非零码退出,方便外部脚本感知
    if manifest["summary"].get("ok", 0) != manifest["summary"]["total"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
