"""从宽表 CSV 生成 bad case Excel(4 个 sheet,只读 CSV、只写 xlsx)。

取代 `.tmp/` 下四个接力脚本:
  make_badcase_top200.py    -> token_top200 / cache_miss_top200 / latency_top200
  append_badcase_3dim.py    -> badcase_3dim(中间表,已被下方 DROP 逻辑淘汰)
  append_insight_sheets.py  -> deadturn_attribution / highrisk_cost_profile(同上)
  rebuild_badcase_sheet.py  -> badcase(依赖上面三个脚本先产出的文件)

旧链路的问题:三个 top200 表由 `Workbook()` 从零新建,而 `badcase` 由
`load_workbook()` 读现有文件追加——重建 xlsx 必须先跑前者、再跑后者,
且顺序错了不报错,只会静默留下"新 top200 + 旧 badcase"的混合文件。
本脚本一次构建整个工作簿,消除该隐式依赖。

口径(与报告第 4 节共用,阈值常量必须与 generate_report.py 同批修改):
  死亡回合 : turn_completion_rate == 0(未闭环)
  高危回合 : 闭环 ∧ (业务错误数 be >= HIGH_BE_THRESHOLD
                      ∨ 工具错误数 te > HIGH_TOOL_ERROR_THRESHOLD)
  判定顺序 : 死亡优先——已判为死亡的回合不再进高危集合,两类互斥。
             这是 2026-09-22 需求方确认的既有顺序,不是遗漏。

原因列由宽表单元格实时推导,不引入任何宽表外的数据;模板分支与旧脚本逐字一致,
以便产出与历史文件可横向比对。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

# 高危阈值(2026-09-24 需求方确认)。与
# `.agents/skills/metric-report/scripts/generate_report.py` 的同名常量必须一致 ——
# 报告第 4 节与 bad case sheet 的高危口径不允许分叉,改一处必须同批改另一处。
HIGH_BE_THRESHOLD = 3
HIGH_TOOL_ERROR_THRESHOLD = 10

# 三张 top200 表:(宽表列名, sheet 名, 排序方向, 数值形态)
# cache_input_token_ratio 保留原字符串:该列是浮点比率,转 int 会丢精度。
TOP_SHEETS: tuple[tuple[str, str, str, str], ...] = (
    ("total_token_consumption", "token_top200", "desc", "int"),
    ("cache_input_token_ratio", "cache_miss_top200", "asc", "raw"),
    ("end_to_end_latency_ms", "latency_top200", "desc", "int"),
)

TOP_N = 200
BADCase_SHEET = "badcase"

# badcase 表列宽(与历史文件一致,便于人工阅读原因列)
BADCase_COL_WIDTHS: Mapping[str, float] = {
    "A": 10,
    "B": 11,
    "C": 30,
    "D": 34,
    "E": 11,
    "F": 14,
    "G": 9,
    "H": 72,
}


def _cell(row: Mapping[str, str], column: str) -> dict[str, Any]:
    """读宽表 JSON 单元格的首元素;空串、非法 JSON 一律返回空 dict。

    宽表 JSON 列统一是"单元素数组"形态(见 reporters.metric_table);空串表示
    该日志没有该类事件(零错误/零重试留空),不是"值为 0"。
    """
    raw = row.get(column) or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return {}


def _int_field(row: Mapping[str, str], column: str, key: str) -> int:
    value = _cell(row, column).get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _retry_rate(row: Mapping[str, str]) -> float | None:
    value = _cell(row, "api_retry_rate").get("api_retry_rate")
    return float(value) if isinstance(value, (int, float)) else None


def _business_error_count(row: Mapping[str, str]) -> int:
    return _int_field(row, "api_retry_rate", "api_business_error_count")


def _tool_error_count(row: Mapping[str, str]) -> int:
    return _int_field(row, "tool_error_rate", "error_call_count")


def _llm_error_count(row: Mapping[str, str]) -> int:
    return _int_field(row, "llm_error_count", "llm_error_count")


def _reason_dead(row: Mapping[str, str]) -> str:
    """死亡回合中文原因。按 工具错误 > 模型错误 > 重试率 > 无信号 依次判定。

    注意本函数只看 tool_error_rate / llm_error_count 两个信号,与高危口径的
    api_business_error_count 互不干涉:死亡侧关注"死于哪种错误",高危侧关注
    "撞墙撞了多少次",两者是不同问题,不共用判据。
    """
    tool_errors = _tool_error_count(row)
    model_errors = _llm_error_count(row)
    rate = _retry_rate(row)
    if tool_errors and model_errors:
        return (
            f"回合未闭环;同时发生 {tool_errors} 次工具错误与 {model_errors} 次模型错误,"
            "多类故障叠加导致中断"
        )
    if tool_errors:
        return f"回合未闭环;发生 {tool_errors} 次工具错误,疑似工具失败/约束违规导致中断"
    if model_errors:
        return f"回合未闭环;发生 {model_errors} 次模型层错误,疑似模型调用失败/超时导致中断"
    if rate is not None and rate > 0.4:
        return f"回合未闭环;框架API重试率 {rate:.0%},疑似网络/服务不稳定导致中断"
    return "回合未闭环;无工具/模型错误信号,需人工复查根因(可能是超时强断或用户主动中断)"


def _reason_high(row: Mapping[str, str]) -> str:
    """高危回合中文原因:命中 be 与/或 te,合并为单列陈述(同一 log_id 只写一行)。"""
    business_errors = _business_error_count(row)
    tool_errors = _tool_error_count(row)
    retry_attr = _cell(row, "api_retry_attribution")
    tool_attr = _cell(row, "tool_error_attribution")
    segments: list[str] = []

    if business_errors >= HIGH_BE_THRESHOLD:
        # 全角 ≥ 与历史文件逐字对齐:该字符经 _cn_commas 之外的路径原样写入,
        # 换成半角 ">=" 会让全部 be 分支行与历史产出不一致(无法横向比对)。
        segment = f"框架层业务错误 {business_errors} 次(≥{HIGH_BE_THRESHOLD})"
        by_tool = retry_attr.get("by_tool") or []
        by_endpoint = retry_attr.get("by_endpoint") or []
        if by_tool and isinstance(by_tool[0], dict) and by_tool[0].get("name"):
            segment += f",主因工具 {by_tool[0]['name']}"
        if by_endpoint and isinstance(by_endpoint[0], dict) and by_endpoint[0].get("name"):
            endpoint = str(by_endpoint[0]["name"]).split("/")[-1]
            segment += f",接口 {endpoint}"
        retry_after_success = retry_attr.get("retry_after_success", 0)
        if isinstance(retry_after_success, int) and retry_after_success:
            segment += f";其中 {retry_after_success} 次为前置成功仍重发(框架无效重发)"
        segments.append(segment)

    if tool_errors > HIGH_TOOL_ERROR_THRESHOLD:
        segment = f"工具错误 {tool_errors} 次(>{HIGH_TOOL_ERROR_THRESHOLD})"
        by_tool = [x for x in (tool_attr.get("by_tool") or []) if isinstance(x, dict)]
        if by_tool:
            segment += ",集中在 " + "、".join(
                f"{x.get('name')}({x.get('count')}次)" for x in by_tool[:3]
            )
        by_attribution = [x for x in (tool_attr.get("by_attribution") or []) if isinstance(x, dict)]
        if by_attribution:
            segment += ";归因 " + "、".join(
                f"{x.get('name')}({x.get('count')}次)" for x in by_attribution
            )
        segments.append(segment)

    return "闭环但" + ";".join(segments) + ",反复撞墙成本高"


def _sort_key(row: Mapping[str, str], column: str) -> float | None:
    raw = row.get(column) or ""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_top_sheets(workbook: Workbook, rows: Sequence[Mapping[str, str]]) -> None:
    """写三张 top200 表:按主指标排序取前 N,身份列恒输出,不可计算行不参与排序。"""
    bold = Font(bold=True)
    for metric, sheet_name, direction, kind in TOP_SHEETS:
        scored: list[tuple[float, Mapping[str, str]]] = []
        for row in rows:
            value = _sort_key(row, metric)
            if value is None:
                continue
            scored.append((value, row))
        scored.sort(key=lambda item: item[0], reverse=(direction == "desc"))

        target = workbook.create_sheet(title=sheet_name)
        target.append(["uid", "session_id", "msg_id", metric])
        for cell in target[1]:
            cell.font = bold
        for _, row in scored[:TOP_N]:
            raw = row[metric]
            # 整数列取整;比率列保留原字符串,避免浮点精度损失
            written: object = int(round(float(raw))) if kind == "int" else raw
            target.append([row["user_id"], row["session_id"], row["log_id"], written])


def _write_badcase_sheet(workbook: Workbook, rows: Sequence[Mapping[str, str]]) -> tuple[int, int]:
    """写 badcase 表,返回 (死亡条数, 高危条数)。

    判定顺序:死亡优先——已判为死亡的回合不再进高危集合,两类互斥。
    """
    dead: list[list[object]] = []
    high: list[list[object]] = []
    for row in rows:
        rate = _retry_rate(row)
        retry_text = f"{rate:.0%}" if rate is not None else ""
        common = [
            row["user_id"],
            row["session_id"],
            row["log_id"],
            row["tool_calls_count"],
            row["total_token_consumption"],
            retry_text,
        ]
        if row["turn_completion_rate"] == "0":
            dead.append(["死亡回合", *common, _reason_dead(row)])
            continue
        if (
            _business_error_count(row) >= HIGH_BE_THRESHOLD
            or _tool_error_count(row) > HIGH_TOOL_ERROR_THRESHOLD
        ):
            high.append(["高危回合", *common, _reason_high(row)])

    # 两个集合各自按 token 消耗降序,便于优先打捞最贵的
    def token_key(item: Sequence[object]) -> float:
        raw = str(item[5] or "")
        try:
            return float(raw)
        except ValueError:
            return 0.0

    dead.sort(key=token_key, reverse=True)
    high.sort(key=token_key, reverse=True)

    target = workbook.create_sheet(title=BADCase_SHEET)
    target.append(
        [
            "分类",
            "uid",
            "session_id",
            "msg_id",
            "工具调用数",
            "token消耗",
            "重试率",
            "具体原因(中文)",
        ]
    )
    for cell in target[1]:
        cell.font = Font(bold=True)
    for item in (*dead, *high):
        target.append(item)

    # 原因列较长,必须换行 + 顶对齐,否则人工阅读时会被列宽截断视觉
    for row in target.iter_rows(min_row=2):
        row[7].alignment = Alignment(wrap_text=True, vertical="top")
    for column, width in BADCase_COL_WIDTHS.items():
        target.column_dimensions[column].width = width

    return len(dead), len(high)


def build_workbook(csv_path: Path, output_path: Path) -> dict[str, int]:
    """读宽表、构建 4 个 sheet 并写盘;返回各表行数摘要。"""

    rows = _load_rows(csv_path)
    workbook = Workbook()
    workbook.remove(workbook.active)  # 移除 openpyxl 默认空 sheet

    _write_top_sheets(workbook, rows)
    dead_count, high_count = _write_badcase_sheet(workbook, rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return {
        "row_count": len(rows),
        "dead_count": dead_count,
        "high_count": high_count,
        "sheet_count": len(workbook.sheetnames),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/processed/metric_table_full.csv"),
        help="宽表 CSV 路径",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/badcase_top200.xlsx"),
        help="输出的 xlsx 路径",
    )
    args = parser.parse_args()
    summary = build_workbook(args.csv, args.output)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print(f"已写出: {args.output}")


if __name__ == "__main__":
    main()
