"""backfill_tool_error_attribution 纯函数行为测试(不触 OSS)。

覆盖:空/零错误返回空串、单元素 JSON 数组包围、by_tool 与 by_attribution 的
计数与稳定排序(次数降序 + 名称升序)、不截断尾部、缺工具名/归因的保守降级。
"""

import json

from scripts.backfill_tool_error_attribution import build_cell


def test_build_cell_empty_returns_empty_string() -> None:
    """无工具错误(或归因不可计算)的行必须留空,不得写 0——空 ≠ 零。"""

    assert build_cell(()) == ""
    assert build_cell(None) == ""


def test_build_cell_wraps_single_element_json_array() -> None:
    """单元格与宽表其它 JSON 列同形态:统一 [] 包围的单元素数组。"""

    cell = build_cell(
        (
            {"tool_name": "write", "attribution": "model_misuse"},
            {"tool_name": "search", "attribution": "infrastructure_failure"},
        )
    )
    arr = json.loads(cell)
    assert isinstance(arr, list) and len(arr) == 1
    assert list(arr[0].keys()) == ["by_tool", "by_attribution"]


def test_build_cell_counts_and_orders_by_count_desc_then_name() -> None:
    """按次数降序、同名次按名称升序,保证同一日志多次运行输出逐字节稳定。"""

    cell = build_cell(
        (
            {"tool_name": "search", "attribution": "model_misuse"},
            {"tool_name": "write", "attribution": "model_misuse"},
            {"tool_name": "write", "attribution": "data_availability"},
            {"tool_name": "write", "attribution": "model_misuse"},
            {"tool_name": "alpha", "attribution": "unclassified"},
            {"tool_name": "alpha", "attribution": "model_misuse"},
        )
    )
    payload = json.loads(cell)[0]
    assert payload["by_tool"] == [
        {"name": "write", "count": 3},
        {"name": "alpha", "count": 2},
        {"name": "search", "count": 1},
    ]
    assert payload["by_attribution"] == [
        {"name": "model_misuse", "count": 4},
        {"name": "data_availability", "count": 1},
        {"name": "unclassified", "count": 1},
    ]


def test_build_cell_does_not_truncate_tail() -> None:
    """不做 Top-K 截断:长尾工具正是"撞墙最严重"的日志,截掉会丢定位能力。

    与 api_retry_attribution 的 Top5 口径刻意不同,故单列断言上限行为。
    """

    calls = tuple({"tool_name": f"tool_{i:02d}", "attribution": "model_misuse"} for i in range(12))
    payload = json.loads(build_cell(calls))[0]
    assert len(payload["by_tool"]) == 12
    assert sum(item["count"] for item in payload["by_tool"]) == 12
    assert sum(item["count"] for item in payload["by_attribution"]) == 12


def test_build_cell_falls_back_for_missing_fields() -> None:
    """缺工具名/归因时用占位符而不是丢弃该次错误:计数守恒优先于命名准确。

    丢弃会让 by_tool 之和小于 error_call_count,破坏与值列的交叉校验。
    """

    payload = json.loads(build_cell(({"tool_name": None, "attribution": None},)))[0]
    assert payload["by_tool"] == [{"name": "<unmapped>", "count": 1}]
    assert payload["by_attribution"] == [{"name": "unclassified", "count": 1}]
