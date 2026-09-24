"""从 metric_table_full.csv 生成自包含 HTML 指标报告(图表内嵌 base64 PNG)。

单一数据源:data/processed/metric_table_full.csv(全量研究模式宽表)。
零回源:逐工具耗时/token、稳定性信号、框架重试均已落在宽表 JSON 单元格内,
本脚本只做聚合与可视化,不访问 OSS。

运行(需 matplotlib+numpy,项目 venv 未装,用 anaconda python):
  "C:/Users/EDY/anaconda3/python.exe" \
    .agents/skills/metric-report/scripts/generate_report.py \
    --csv data/processed/metric_table_full.csv \
    --output reports/metric_report.html \
    --data-range 20260915-20260922

`--data-range` 为必填:报告表头必须标明本批次日志的数据日期区间(2026-09-24
需求方要求)。宽表不存日期字段,无法从 CSV 推导,须由调用方按本次导出清单的
实际日期范围传入;格式 `YYYYMMDD-YYYYMMDD`。

输出:自包含 HTML(图表内嵌),无外部依赖,浏览器直接打开。
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# 微软雅黑置于首位:SimHei 无 bold 字重(fontweight="bold" 会静默回退 400),
# 雅黑含真 Bold,分位数标签等加粗文字才能真正"变大变清晰"。
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun"]
plt.rcParams["axes.unicode_minus"] = False

# 标量数值列:(列名, 类型, 是否比率, 中文指标名, 是否进分位数表)
# tool_calls_count 按 2026-09-23 需求方要求删除"工具调用总次数"的统计展示
# (分位数表与分布图均不再出现),仅保留第 0 节可计算性清单与第 1 节列集清单。
SCALAR_COLS = [
    ("tool_calls_count", "int", False, "工具调用总次数", False),
    ("total_token_consumption", "int", False, "端到端总 token 消耗", True),
    ("end_to_end_latency_ms", "int", False, "端到端总耗时", True),
    ("output_token_ratio", "float", True, "输出 token 比率", True),
    ("cache_input_token_ratio", "float", True, "缓存token占输入token比率", True),
    ("answer_evidence_grounding_rate", "float", True, "数值锚定率", True),
    # 压缩事件数为稀疏计数(绝大多数为 0),分位数与均值无解读价值,不进分位数表
    ("compaction_event_count", "int", False, "上下文压缩事件数", False),
    # 回合完成率为 0/1 标记,分位数无意义;它是第 2.3/4 节的被解释变量,不进分位数表
    ("turn_completion_rate", "int", False, "回合完成率", False),
]

# 单元素 JSON 数组、但不作逐工具聚合也不作稳定性信号的列:只进第 0 节可计算性清单。
# max_tool_* 两列虽含 tool_name,但每行只有一条记录,按工具聚合会退化成单点排名,
# 故不进 PER_TOOL_COLS;它们是消耗/时延的集中度结论,不是稳定性信号,故不进 JSON_SCALAR。
# api_retry_attribution 由第 3.5 节单独消费;tool_error_attribution 由第 3.3 节
# 单独消费(见 `chart_tool_error_attribution` / `chart_tool_error_rate_and_matrix`)。
JSON_EXTRA_COLS = [
    "max_tool_token_share",
    "max_tool_latency_share",
    "api_retry_attribution",
    "tool_error_attribution",
]

# 逐工具 JSON 列表列 -> 取值键
PER_TOOL_COLS = [
    ("per_tool_call_latency_ms", "per_tool_call_latency_ms", "耗时(ms)"),
    ("per_tool_call_tokens", "per_tool_call_tokens", "Token"),
]

# 单元素 JSON 对象列 -> 提取键(用于稳定性统计)
JSON_SCALAR = {
    "continuation_restart_count": "continuation_restart_count",
    "llm_error_count": "llm_error_count",
    "tool_error_rate": "error_call_count",
    "error_recovery_rate": "error_recovered",
    "api_retry_rate": "api_retry_rate",
}

# 高危日志阈值(2026-09-24 需求方确认):闭环 ∧ (业务错误数 be>=3 ∨ 工具错误数 te>10)。
# 与 `.tmp/rebuild_badcase_sheet.py` 的同名常量必须一致——报告与 bad case sheet
# 的高危口径不允许分叉。改这里必须同步改那边(以及 SKILL.md 的「关键口径」)。
HIGH_BE_THRESHOLD = 3
HIGH_TOOL_ERROR_THRESHOLD = 10

# 模型错误分类目录(llm_error_class_v1):分类图必须穷举这 6 类,零命中类也要列出。
# 与 src/collectors/llm_error_count.py 的 _ERROR_CLASS_RULES_V1 同步;目录升版时同步此处。
LLM_ERROR_CLASS_CATALOG = (
    "context_overflow",
    "provider_timeout",
    "queue_timeout",
    "billing_failure",
    "protocol_corruption",
    "unclassified",
)

# 第 1 节列集演进:只列中文指标名,顺序即宽表列序。新增/删除指标列时同步此处。
EVOLUTION_KEPT = [
    "工具调用总次数",
    "单工具 token 消耗",
    "端到端总 token 消耗",
    "单工具最大 token 消耗比率",
    "输出 token 比率",
    "缓存 token 比率",
    "单工具调用时延",
    "端到端总耗时",
    "单工具最大时延比率",
    "数值锚定率",
]
EVOLUTION_DELETED = [
    "单工具调用重复次数",
    "MCP 工具调用膨胀率",
    "引用可溯率",
]
EVOLUTION_ADDED = [
    "回合完成率",
    "续跑重启次数",
    "模型层错误数与错误分类",
    "工具错误率与错误归因",
    "工具错误归因明细",
    "错误自恢复率",
    "上下文压缩事件数",
    "框架 API 重试率",
    "框架 API 重试归因",
]

# 新增指标怎么读:一句话语义 + 取值举例(示例是取值含义说明,不是本样本统计值)
ADDED_NOTES = [
    (
        "回合完成率",
        "这条日志有没有跑到终点。1 = 正常交付回答;0 = 中途断掉(报错而死或静默中断);"
        "报告级 = 完成日志数 ÷ 总日志数。",
    ),
    (
        "续跑重启次数",
        "断了之后框架自动重跑了几次,并同时给出是否闭环。例如「重启 1 次 + 闭环 1」表示断过一次、"
        "最终救回来了;「重启 2 次 + 闭环 0」表示重启两次仍然失败。",
    ),
    (
        "模型层错误数与错误分类",
        "模型调用本身报错的次数,并按错误文本归到五类(上下文溢出 / 供应商超时 / 排队超时 / "
        "计费失败 / 协议损坏)。例如某条日志出现 1 次 context_overflow,说明它是上下文撑爆而死。",
    ),
    (
        "工具错误率与错误归因",
        "工具调用失败占比,并把每次失败归到一类:模型用错(改提示词)、基础设施(改重试/供应商)、"
        "数据不存在(环境事实)、降级返回(不记失败)。例如 edit 报「oldString 有多个匹配」归模型用错。",
    ),
    (
        "工具错误归因明细",
        "逐条列出失败的到底是哪个工具、各归到哪一类(by_tool + by_attribution),不做 Top-K 截断。"
        "例如「bash 12 次·模型误用」表示坏在 bash 且多数是模型用法问题。"
        "只有发生错误终态的日志有值,零错误日志留空——留空不等于 0 次错误。",
    ),
    (
        "错误自恢复率",
        "出过错的日志里最终仍然交付的比例。出错但闭环且有正文 = 1;出错且没交付 = 0;"
        "整条日志没出过错的不进分母(不是 1 也不是 0)。",
    ),
    (
        "上下文压缩事件数",
        "系统自动压缩上下文的次数。0 = 没压过;数字越大说明该日志上下文越快撑爆,"
        "是上下文溢出报错的前兆信号。",
    ),
    (
        "框架 API 重试率",
        "工具内部传输层自动重发的请求占比。重试判定为同一 (callID, endpoint+入参指纹) 重复出现"
        "(fingerprint_dup_v2),不是请求 ID 尾序号——尾序号实为「调用内第 N 个请求」的工作流步骤。"
        "数字越大说明工具层在反复撞墙。注意这是框架/传输层重发,不是模型级重试;模型级重试当前不可观测。",
    ),
    (
        "框架 API 重试归因",
        "框架层重复请求集中在哪个工具、哪个接口(by_tool / by_endpoint,各取 Top5),"
        "以及其中「上一次已成功框架仍原样重发」的次数(retry_after_success)。"
        "例如「mkdir 1431 次 / node-id-by-full-path 95.6%」即重复几乎全部砸在这一个接口上。"
        "只有重试率 > 0 的日志有值,零重试留空——留空不等于零重试。",
    ),
]


def pct(vals, p):
    s = sorted(vals)
    if not s:
        return None
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load_rows(csv_path):
    with csv_path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _j(r, col):
    if not r.get(col):
        return None
    try:
        return json.loads(r[col])
    except Exception:
        return None


def jnum(r, col, key):
    obj = _j(r, col)
    if not obj or not isinstance(obj, list) or not obj:
        return 0
    v = obj[0].get(key)
    return v if isinstance(v, (int, float)) else 0


def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def collect_per_tool(rows, col, value_key):
    agg = defaultdict(list)
    for r in rows:
        obj = _j(r, col)
        if not isinstance(obj, list):
            continue
        for o in obj:
            name = o.get("tool_name")
            v = o.get(value_key)
            if name is not None and isinstance(v, (int, float)):
                agg[name].append(v)
    return agg


def collect_tool_presence(rows, col) -> Counter:
    """每个工具在多少条日志里至少出现过一次(出现日志数)。

    与 collect_per_tool 的区别:后者按"有数值的条目"计数,会把值为 null 的
    条目(耗时不可观测)漏掉;频次口径要的是"日志里有没有这个工具",因此只看
    tool_name 是否存在,不要求取值可计算。单元为日志,不是调用次数。
    """
    presence: Counter = Counter()
    for r in rows:
        obj = _j(r, col)
        if not isinstance(obj, list):
            continue
        seen = set()
        for o in obj:
            name = o.get("tool_name")
            if isinstance(name, str) and name and name not in seen:
                seen.add(name)
                presence[name] += 1
    return presence


def _ratio(num: float, denom: float) -> float:
    """防零分母的除法:分母为 0 时返回 0.0,由调用方决定如何展示。

    第 4/5 节的洞察数字全部取自真实样本,分母(如工具错误总数、长日志条数)
    理论上可能为 0。给 0 而不是抛 ZeroDivisionError,是为了让空样本也能出报告。
    """
    return num / denom if denom else 0.0


def fmt_num(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return f"{v:,}"


def _cn_commas(html: str) -> str:
    """把正文的半角逗号统一为中文逗号,避免中文句子里文字挤在一起。

    只处理 </style> 之后的正文(CSS 的 font-family 列表等依赖半角逗号);
    数字千分位(如 1,234 / 1,234,567)与内嵌图片 data URI 的 "base64," 用占位符
    保护,不被替换。
    图表为内嵌 PNG,图内文字需在绘图字符串里直接写中文逗号,本函数管不到。
    """
    head, sep, body = html.partition("</style>")
    body = body.replace(";base64,", ";base64\x01")
    body = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "\x00", body)
    body = body.replace(",", "，").replace("\x00", ",").replace("\x01", ",")
    return head + sep + body


# ---------------- 图表 ----------------


def build_evolution_section() -> str:
    """第 1 节:两版指标列集增删对照表(仅中文指标名)+ 新增指标含义注释。"""
    cols = [
        ("保留指标", EVOLUTION_KEPT),
        ("删除指标", EVOLUTION_DELETED),
        ("新增指标", EVOLUTION_ADDED),
    ]
    head = "".join(f"<th>{name}（{len(items)}）</th>" for name, items in cols)
    body = ""
    for i in range(max(len(items) for _, items in cols)):
        cells = "".join(f"<td>{items[i] if i < len(items) else ''}</td>" for _, items in cols)
        body += f"<tr>{cells}</tr>"
    notes = "".join(
        f"<tr><td>{name}</td><td class='desc'>{desc}</td></tr>" for name, desc in ADDED_NOTES
    )
    return f"""<table class="left"><tr>{head}</tr>{body}</table>
<p class="note">旧版 = 核心批次宽表列集（{len(EVOLUTION_KEPT) + len(EVOLUTION_DELETED)} 指标列：
保留 {len(EVOLUTION_KEPT)} + 后续删除 {len(EVOLUTION_DELETED)}）；现版 = 稳定性批次完成后
（{len(EVOLUTION_KEPT) + len(EVOLUTION_ADDED)} 指标列：保留 {len(EVOLUTION_KEPT)} + 新增
{len(EVOLUTION_ADDED)}）。删除的三项评测器与测试保留，只是不再进 CSV 宽表。</p>
<h3>新增指标怎么读</h3>
<table class="left"><tr><th>新增指标</th><th>含义与取值举例</th></tr>{notes}</table>
<p class="note">上表举例只说明取值含义，不是本样本的统计结果；本样本实测值见第 2 节。</p>"""


def build_sample_summary(rows, csv_name: str) -> str:
    """报告头:样本规模与逐指标可计算日志数(可计算 = 该列单元格非空)。

    列集常量是宽表列集的镜像:一旦宽表加了列而这里没同步,第 0 节会静默漏列。
    因此这里反向比对一次宽表表头,把未被任何常量覆盖的列显式列出来提醒同步。
    """
    n = len(rows)
    users = len({r.get("user_id", "") for r in rows if r.get("user_id")})
    sessions = len({r.get("session_id", "") for r in rows if r.get("session_id")})
    modes = sorted({r.get("mode", "") for r in rows if r.get("mode")})

    # 可计算口径:标量列/逐工具列/JSON 列统一按"单元格非空"计数
    # 取值形态只允许三种:整数 / 浮点数 / JSON(即宽表单元格实际写入的形态)
    comp_cols = (
        [(c, "整数" if typ == "int" else "浮点数") for c, typ, _, _, _ in SCALAR_COLS]
        + [(c, "JSON") for c, _, _ in PER_TOOL_COLS]
        + [(c, "JSON") for c in JSON_SCALAR]
        + [(c, "JSON") for c in JSON_EXTRA_COLS]
    )
    comp_rows = ""
    for col, kind in comp_cols:
        ok = sum(1 for r in rows if (r.get(col) or "").strip())
        comp_rows += (
            f"<tr><td>{col}</td><td>{kind}</td><td>{ok:,}</td>"
            f"<td>{ok / n:.1%}</td><td>{n - ok:,}</td></tr>"
        )

    identity_cols = ("user_id", "session_id", "log_id", "mode")
    listed = {c for c, _ in comp_cols}
    unlisted = [c for c in (rows[0] if rows else {}) if c not in listed and c not in identity_cols]
    drift_note = ""
    if unlisted:
        drift_note = (
            f'<p class="note"><b>列集漂移提醒</b>：宽表有以下列未被脚本任何列集常量覆盖 —— '
            f"{'、'.join(unlisted)}。请按 SKILL.md「维护」把它们补进 "
            f"SCALAR_COLS / PER_TOOL_COLS / JSON_SCALAR / JSON_EXTRA_COLS，"
            f"否则本节的可计算性清单不完整。</p>"
        )

    return f"""<table class="left">
<tr><th>日志总数</th><th>用户数</th><th>会话数</th><th>模式</th><th>数据源文件</th></tr>
<tr><td>{n:,}</td><td>{users:,}</td><td>{sessions:,}</td>
<td>{" / ".join(modes) or "-"}</td><td>{csv_name}</td></tr>
</table>
<h3>逐指标可计算日志数</h3>
<table class="left"><tr><th>指标列</th><th>取值形态</th><th>可计算日志数</th><th>占比</th>
<th>不可计算</th></tr>{comp_rows}</table>
<p class="note">可计算 = 该指标列在本日志上给出了值;不可计算含未闭环等结构性缺口与
NOT_COMPUTABLE 语义(如无工具调用、无压缩通道),细分原因见各节诊断。</p>{drift_note}"""


def _fmt_axis(v: float, unit: float, unit_label: str) -> str:
    """坐标轴/标签数值的紧凑写法(按量级自适应小数位)。"""
    if v == 0:
        return "0"
    x = v / unit
    if unit == 1.0:
        return f"{x:,.0f}"
    if abs(x) >= 100:
        return f"{x:,.0f}{unit_label}"
    if abs(x) >= 1:
        return f"{x:,.1f}{unit_label}"
    return f"{x:,.2f}{unit_label}"


def _spread_labels(ys, lo, hi, min_gap_frac=0.06):
    """把过近的标签锚点沿轴拉开,避免文字重叠。只移动标签位置,不动实际分位值。"""
    ys = list(ys)
    if hi <= lo or not ys:
        return ys
    gap = (hi - lo) * min_gap_frac
    out = sorted(ys)
    for i in range(1, len(out)):
        if out[i] - out[i - 1] < gap:
            out[i] = out[i - 1] + gap
    over = out[-1] - hi
    if over > 0:
        out = [v - over for v in out]
    # 还原到原顺序(调用方按 labels 顺序取用)
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    back = [0.0] * len(ys)
    for pos, idx in enumerate(order):
        back[idx] = out[pos]
    return back


def _fmt_range(lo: float, hi: float, unit: float, unit_label: str) -> str:
    """分箱数值范围文本,如 "0–10M" / "1.2–2.4万"。"""

    def one(x: float) -> str:
        x = x / unit
        if x == 0:
            return "0"
        if unit == 1.0 or abs(x) >= 100:
            return f"{x:,.0f}"
        if abs(x) >= 1:
            return f"{x:,.1f}"
        return f"{x:,.2f}"

    if unit == 1.0:
        return f"{one(lo)}–{one(hi)}"
    return f"{one(lo)}–{one(hi)}{unit_label}"


def chart_percentile_bars(rows) -> str:
    """两指标 × 2 列:左列等宽分箱频次直方图,右列 p50/p80/p95/p99 刻度线标签。

    纵轴上限截断到 p99:极端离群值会把线性轴拉开,使 p50~p99 挤在轴底无法区分;
    超出 p99 的样例不计入直方图与坐标轴,条数在图上单独标注。
    """
    import numpy as np

    specs = [
        ("total_token_consumption", "端到端总 token 消耗", 1e6, "M"),
        ("end_to_end_latency_ms", "端到端总耗时", 60000, "min"),
    ]
    pcts = [0.5, 0.8, 0.95, 0.99]
    labels = ["p50", "p80", "p95", "p99"]
    colors = ["#f57f17", "#2e7d32", "#1565c0", "#c62828"]
    n_bins = 12

    fig, axes = plt.subplots(
        2,
        2,
        figsize=(13, 8.8),
        gridspec_kw={"width_ratios": [3, 0.8], "wspace": 0.03, "hspace": 0.55},
    )
    for row_i, (col, title, unit, unit_label) in enumerate(specs):
        vals = []
        for r in rows:
            v = r.get(col)
            if not v:
                continue
            try:
                vals.append(float(v))
            except ValueError:
                pass
        ax_h, ax_p = axes[row_i]
        if not vals:
            ax_h.set_title(f"{title}(无数据)", fontsize=11)
            ax_p.set_visible(False)
            continue

        pvals = [pct(vals, p) for p in pcts]
        cap = pvals[-1]  # p99 作为坐标轴上界,屏蔽离群值
        kept = [v for v in vals if v <= cap]
        excluded = len(vals) - len(kept)

        # 线性轴、底边取 0:等宽分组在等距刻度下视觉等宽
        hi_u = cap / unit
        binw = hi_u / n_bins
        edges = np.linspace(0.0, cap, n_bins + 1)
        counts, _ = np.histogram(kept, bins=edges)
        edges_u = edges / unit
        centers = (edges_u[:-1] + edges_u[1:]) / 2
        y_bottom, y_top = -binw * 0.6, hi_u + binw * 1.2

        # 左:等宽分箱频次
        ax_h.barh(
            centers,
            counts,
            height=binw * 0.78,
            color="#90a4ae",
            alpha=0.9,
            edgecolor="#607d8b",
            linewidth=0.6,
        )
        ax_h.set_xlabel("频次(条)", fontsize=12)
        ax_h.set_title(f"{title}:分箱频次(等宽分组)", fontsize=13, pad=14)
        # 参考 3.1:数值标签放条尾、不加白底,靠 xlim 留白避免文字压柱
        ax_h.set_xlim(0, max(counts) * 1.3)
        for c, cnt in zip(centers, counts):
            ax_h.text(cnt, c, f" {cnt} 条", va="center", ha="left", fontsize=9)

        # 纵轴刻度落在分箱中点、等距,标签即该分箱的数值范围
        ax_h.set_yticks(centers)
        ax_h.set_yticklabels(
            [_fmt_range(edges[i], edges[i + 1], unit, unit_label) for i in range(n_bins)],
            fontsize=10,
        )
        ax_h.tick_params(axis="x", labelsize=11)
        if excluded:
            ax_h.text(
                0.985,
                0.96,
                f"{excluded} 条 > {labels[-1]} 未入图",
                transform=ax_h.transAxes,
                ha="right",
                va="top",
                fontsize=10,
                color="#c62828",
            )

        # 右:只画分位刻度线,数值标签字号加大;两列紧邻,虚线读作一条
        ax_p.set_xticks([])
        ax_p.set_yticks([])
        ax_p.set_xlim(0, 1)
        ax_p.set_title(f"{title}:分位数", fontsize=13, pad=14)
        lab_pos = _spread_labels([v / unit for v in pvals], y_bottom, y_top, min_gap_frac=0.09)
        off = (y_top - y_bottom) * 0.02
        for lab, c, v, vpos in zip(labels, colors, pvals, lab_pos):
            for ax in (ax_h, ax_p):
                ax.axhline(v / unit, color=c, linestyle="--", linewidth=1.4, alpha=0.95)
            # 标签置于虚线上方(不压线),不加白底
            ax_p.text(
                0.06,
                vpos + off,
                f"{lab} = {_fmt_axis(v, unit, unit_label)}",
                color=c,
                fontsize=12,
                fontweight="bold",
                va="bottom",
                ha="left",
            )

        for ax in (ax_h, ax_p):
            ax.set_ylim(y_bottom, y_top)
        for side in ("top", "right", "left"):
            ax_p.spines[side].set_visible(False)
    # tight_layout 与本图(双列宽比 + transAxes 注释)不兼容会告警,改显式边距:
    # 左侧留给分箱范围标签,行间距由 gridspec hspace 控制,避免标题/xlabel 相压。
    fig.subplots_adjust(left=0.09, right=0.99, top=0.95, bottom=0.06)
    return fig_to_b64(fig)


def chart_tool_rank(agg, presence, title, value_label, n_logs, top=15, unit=""):
    """工具耗时/Token 排名:条形长度为平均单次调用值,标注 n 为出现日志数。

    标注刻意用"出现日志数"而非调用次数,与第 3.1/3.2 节表格口径统一。
    """
    stats = []
    for name, vals in agg.items():
        stats.append((name, statistics.mean(vals), statistics.median(vals), presence.get(name, 0)))
    stats.sort(key=lambda x: -x[1])
    top_n = stats[:top]
    names = [s[0] for s in top_n]
    means = [s[1] for s in top_n]
    fig, ax = plt.subplots(figsize=(7, 5))
    y = range(len(names))
    ax.barh(list(y), means, color="#1565c0", alpha=0.8, height=0.6)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel(value_label)
    ax.set_title(f"{title}(按平均单次调用，Top{top})", fontsize=11)
    for yi, (name, mean, med, freq) in zip(y, top_n):
        ax.text(mean, yi, f" {mean:,.0f}{unit} ({freq:,} 条日志)", va="center", fontsize=7)
    return fig_to_b64(fig)


def chart_tool_freq(presence: Counter, n_logs: int, top=15):
    """工具使用频率排名:纵轴为"出现该工具的日志数",不是调用次数。

    标注格式 "N 条日志(M%)":M 为该工具出现日志数占全样本日志数的比例,
    避免读者把纵轴误读成调用次数。
    """
    stats = presence.most_common(top)
    names = [s[0] for s in stats]
    freqs = [s[1] for s in stats]
    fig, ax = plt.subplots(figsize=(7, 5))
    y = range(len(names))
    ax.barh(list(y), freqs, color="#ef6c00", alpha=0.8, height=0.6)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("出现该工具的日志数(条)")
    ax.set_title(f"工具使用频率 Top{top}(按出现日志数，非调用次数)", fontsize=11)
    for yi, f in zip(y, freqs):
        ax.text(f, yi, f" {f:,} 条日志({f / n_logs:.0%})", va="center", fontsize=8)
    return fig_to_b64(fig)


def build_stability_signals(rows) -> list[tuple[str, int, str, int]]:
    """稳定性维度信号:标签 + 命中日志数 + 定义与举例 + 命中中未闭环数。

    返回四元组,按命中日志数从高到低排序;图表与下方表格共用这一份数据,
    避免两处口径漂移。

    2026-09-24 需求方确认:API 重试信号改用 fingerprint_dup_v2 口径、阈值取
    p90≈0.15(旧 0.6 在新口径下几乎为 0,无判别力);并新增「API业务错误≥3」
    信号(高消耗/死亡双重富集最强),与第 4 节高危口径对齐——高危口径此后
    扩展为 be≥3 ∨ te>10,本表的两个信号分行展示,不作高危险替代。
    2026-09-24 后续需求:移除「死亡富集」倍数列(曾为第五元素),2.3 只保留
    命中数与命中中未闭环数两个原始计数,不做派生倍数展示。
    """
    specs = [
        (
            "日志未闭环",
            lambda r: r.get("turn_completion_rate") == "0",
            "该日志没跑到终点。研究模式以 turn.end 事件为闭环锚点,缺 turn.end 即计入;"
            "聊天模式用回答是否存在做代理判定。例:一条日志调了 60 步工具后中断、"
            "没有 turn.end,记为未闭环;报告级完成率 = 闭环日志数 ÷ 总日志数。",
        ),
        (
            "工具错误>0",
            lambda r: jnum(r, "tool_error_rate", "error_call_count") > 0,
            "该日志内至少有一次工具调用以错误终态收尾。例:edit 工具返回"
            "「oldString 匹配到多处」,归到模型误用类,该日志工具错误计数 +1;"
            "一次调多个工具失败就累加多次。",
        ),
        (
            "模型错误>0",
            lambda r: jnum(r, "llm_error_count", "llm_error_count") > 0,
            "该日志至少出现一次模型层错误(llm.error 事件),按事件原始条数计数、"
            "含重复投影。例:一条日志出现 1 次 context_overflow,记为 1 次模型错误并"
            "归到上下文溢出类;全样本只有研究模式有这条通道。",
        ),
        (
            "上下文压缩>0",
            lambda r: int(r.get("compaction_event_count") or 0) > 0,
            "该日志至少触发过一次系统自动压缩上下文(auto=true),按 part.id 去重计数。"
            "例:某条日志上下文撑爆,先后自动压缩 2 次,记为 2;手动压缩不计入。",
        ),
        (
            "API重试率>0.15",
            lambda r: jnum(r, "api_retry_rate", "api_retry_rate") > 0.15,
            "该日志框架 API 重复请求数 ÷ 调用数 > 0.15(≈全可计算口径 p90)。重试按"
            "fingerprint_dup_v2 判定:同一 (callID, endpoint+入参指纹) 重复出现才算,"
            "不再用 requestId 尾序号。例:一条日志 20 次 API 调用里 4 次是对同一请求的"
            "重发,重试率 0.20,即计入。注意这是工具传输层自动重发,不是模型级重试。",
        ),
        (
            "API业务错误≥3",
            lambda r: jnum(r, "api_retry_rate", "api_business_error_count") >= 3,
            "该日志内带业务错误码(code∉成功集)的 API 响应 ≥3 次,是'反复撞墙'的最强信号"
            "(对高消耗 3.6x、死亡 2.4x 双重富集)。例:一条日志对 node-id-by-full-path "
            "反复查询一个已删除路径,累计 18 次 code=11410,即计入。",
        ),
    ]
    signals = []
    for label, pred, desc in specs:
        hits = [r for r in rows if pred(r)]
        dead_hits = sum(1 for r in hits if r.get("turn_completion_rate") == "0")
        signals.append((label, len(hits), desc, dead_hits))
    signals.sort(key=lambda item: -item[1])
    return signals


def chart_stability_dist(signals, n: int) -> str:
    """稳定性信号频次条形图,条尾标注命中数与占比。

    排序按频次(读者习惯)。2026-09-24 后续需求移除了条尾的死亡富集倍数标注,
    本图只呈现原始计数,派生倍数不在 2.3 展示。
    """
    labels = [s[0] for s in signals]
    counts = [s[1] for s in signals]
    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    y = range(len(labels))
    ax.barh(
        list(y), counts, color="#6a1b9a", alpha=0.9, height=0.6, edgecolor="#4a148c", linewidth=0.6
    )
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    # 参考 3.1:数值标签放条尾、不加白底,靠 xlim 留白避免文字压柱/压轴标签
    ax.set_xlim(0, max(counts) * 1.75 if max(counts) else 1)
    for yi, c in zip(y, counts):
        ax.text(c, yi, f" {c:,} ({c / n:.1%})", va="center", ha="left", fontsize=9)
    ax.set_xlabel("日志数", fontsize=11)
    ax.set_title(f"稳定性维度信号分布(全量 {n:,} 条日志，按频次降序)", fontsize=12, pad=12)
    return fig_to_b64(fig)


def chart_llm_error(rows, extra_note: str = "") -> tuple[str, str, str]:
    """模型层错误:左栏分类分布(穷举目录),右栏模型出错率。返回 (图, 表, 注)。

    2026-09-23 需求方要求:
      1. 分类与模型都穷举——分类按 `llm_error_class_v1` 目录全量列出(含零命中类),
         模型列出宽表里出现过的全部模型(含零错误模型);
      2. 两栏柱粗匀称——用相同的"每类目英寸高度"排版,类目少的栏不会被拉粗;
      3. 分类统计占比(占全部错误),模型统计该模型出错率(错误数 ÷ 该模型调用数)。

    分母来源:`model_calls` / `total_call_count` 由 `llm_error_count` 评测器 1.3.0
    起随单元格输出,与错误归因取自同一路由通道。旧版宽表(1.2.0 及以前)该键缺失,
    此时模型出错率无法计算,图中标注"调用数待补"而不是拿错误数硬算。
    """
    classes, model_errors, model_calls, has_denominator = build_llm_error_stats(rows)
    total_errors = sum(classes.values())
    # 分类穷举:目录内全部类目都在,零命中类同样展示(否则读者会以为该类不存在)
    class_items = [(name, classes.get(name, 0)) for name in LLM_ERROR_CLASS_CATALOG]
    class_items.sort(key=lambda item: (-item[1], item[0]))

    # 模型穷举:优先列有调用记录的模型(含零错误),退化时列出现过错误的模型
    model_names = set(model_calls) | set(model_errors)
    model_items = sorted(
        ((name, model_errors.get(name, 0), model_calls.get(name, 0)) for name in model_names),
        key=lambda item: (-item[2], -item[1], item[0]),
    )
    if not model_items and not class_items:
        fig, ax = plt.subplots(figsize=(6, 2))
        ax.text(0.5, 0.5, "无模型层错误", ha="center", va="center")
        ax.set_axis_off()
        return fig_to_b64(fig), "", ""

    # 每类目固定英寸高度 → 两栏柱粗一致;类目少的栏按自身高度缩短并顶部对齐
    row_in = 0.34
    rows_n = max(len(class_items), len(model_items), 1)
    fig_h = rows_n * row_in + 1.5
    fig = plt.figure(figsize=(12.6, fig_h))
    top = 1 - 0.70 / fig_h

    def _panel(rect, items, values, colors, xlabel, title, label_of):
        height = len(items) * row_in / fig_h
        ax = fig.add_axes([rect[0], top - height, rect[1] - rect[0], height])
        y = list(range(len(items)))
        ax.barh(y, values, color=colors, alpha=0.9, height=0.72, edgecolor="#455a64", linewidth=0.6)
        ax.set_yticks(y)
        ax.set_yticklabels([name for name, *_ in items], fontsize=9.5)
        ax.invert_yaxis()
        ax.set_xlim(0, max(values) * 1.42 if max(values) else 1)
        ax.set_xlabel(xlabel, fontsize=10.5)
        ax.set_title(title, fontsize=12, pad=10)
        # 参考 3.1/3.2:数值标签放条尾、不加白底——白底框会在柱尾压出一块
        # 白色留白遮住柱体;留白改由 xlim 余量(max*1.42)提供。
        for yi, value in zip(y, values):
            ax.text(
                value, yi, f" {label_of(items[yi], value)}", va="center", ha="left", fontsize=9.5
            )
        ax.tick_params(axis="x", labelsize=9.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        return ax

    _panel(
        (0.155, 0.47),
        class_items,
        [c for _, c in class_items],
        "#d32f2f",
        "出现次数",
        f"模型错误分类(目录 {len(class_items)} 类全量)",
        lambda item, value: (
            f"{value:,} 次" + (f"({value / total_errors:.1%})" if total_errors else "")
        ),
    )
    _panel(
        (0.635, 1.0),
        model_items,
        [e for _, e, _ in model_items],
        "#1565c0",
        "出错次数",
        f"模型出错率(出现过的 {len(model_items)} 个模型)",
        lambda item, value: _model_label(item, has_denominator),
    )

    table, note = _llm_error_tables(
        class_items, model_items, total_errors, has_denominator, extra_note
    )
    return fig_to_b64(fig), table, note


def build_api_retry_stats(rows):
    """从宽表聚合新口径(fingerprint_dup_v2)API 重试:率分位、零重试占比、
    框架层重复的 by_tool / by_endpoint、前置成功仍重发占比。"""
    rates, repeats_tool, repeats_ep, ras, tot_repeat = [], Counter(), Counter(), 0, 0
    zero = 0
    for r in rows:
        obj = _j(r, "api_retry_rate")
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            v = obj[0].get("api_retry_rate")
            if isinstance(v, (int, float)):
                rates.append(float(v))
                if v == 0:
                    zero += 1
        attr = _j(r, "api_retry_attribution")
        if isinstance(attr, list) and attr and isinstance(attr[0], dict):
            a = attr[0]
            for t in a.get("by_tool") or []:
                if isinstance(t, dict) and isinstance(t.get("count"), (int, float)):
                    repeats_tool[str(t.get("name") or "?")] += int(t["count"])
            for e in a.get("by_endpoint") or []:
                if isinstance(e, dict) and isinstance(e.get("count"), (int, float)):
                    repeats_ep[str(e.get("name") or "?")] += int(e["count"])
            ras += a.get("retry_after_success", 0) or 0
            tot_repeat += sum(t.get("count", 0) for t in (a.get("by_tool") or []))
    return rates, zero, repeats_tool, repeats_ep, ras, tot_repeat


def chart_api_retry(rows) -> tuple[str, str]:
    """3.5 图:框架层重试按工具 / 按接口双栏;返回 (图, 洞察文字 HTML)。"""
    rates, zero, rtool, rep, ras, tot = build_api_retry_stats(rows)

    def _p(vals, p):
        s = sorted(vals)
        if not s:
            return 0.0
        k = (len(s) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    micro_num = tot  # 框架层重复总数即分子
    micro_den = 0
    for r in rows:
        obj = _j(r, "api_retry_rate")
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            micro_den += obj[0].get("api_call_count", 0) or 0

    tool_items = rtool.most_common(8)
    ep_items = rep.most_common(5)
    row_in = 0.34
    rows_n = max(len(tool_items), len(ep_items), 1)
    fig_h = rows_n * row_in + 1.5
    fig = plt.figure(figsize=(12.6, fig_h))
    top = 1 - 0.70 / fig_h

    def _panel(rect, items, color, xlabel, title):
        height = len(items) * row_in / fig_h
        ax = fig.add_axes([rect[0], top - height, rect[1] - rect[0], height])
        y = list(range(len(items)))
        vals = [c for _, c in items]
        ax.barh(y, vals, color=color, alpha=0.9, height=0.72, edgecolor="#455a64", linewidth=0.6)
        ax.set_yticks(y)
        ax.set_yticklabels([n for n, _ in items], fontsize=9.5)
        ax.invert_yaxis()
        ax.set_xlim(0, max(vals) * 1.5 if max(vals) else 1)
        ax.set_xlabel(xlabel, fontsize=10.5)
        ax.set_title(title, fontsize=12, pad=10)
        for yi, v in zip(y, vals):
            ax.text(v, yi, f"  {v:,} 次", va="center", ha="left", fontsize=9.5)
        ax.tick_params(axis="x", labelsize=9.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    _panel(
        (0.16, 0.47), tool_items, "#d32f2f", "重复请求数", f"框架层重试按工具 Top{len(tool_items)}"
    )
    # 接口名过长会与左栏标签相压,y 轴只显示末段;全路径见 3.5 文字
    ep_disp = [(e.split("/")[-1], c) for e, c in ep_items]
    _panel((0.64, 1.0), ep_disp, "#1565c0", "重复请求数", f"框架层重试按接口 Top{len(ep_disp)}")

    text = f"""<p>新口径(fingerprint_dup_v2)下 API 重试是<b>稀疏信号</b>:可计算
{len(rates):,} 条中零重试 {zero:,} 条({zero / len(rates):.1%});微平均重试率
<b>{micro_num / micro_den:.2%}</b>(分子=框架层重复 {micro_num:,},分母=API 调用 {micro_den:,});
分位 p50={_p(rates, 0.5):.4f} / p90={_p(rates, 0.9):.4f} / p95={_p(rates, 0.95):.4f} /
p99={_p(rates, 0.99):.4f}。</p>
<p><b>关键洞察:重试高度集中且多为无效重发。</b>框架层重复 {tot:,} 次里,
<b>{ras:,} 次({ras / tot:.1%})是"前一次已成功、框架仍重发"</b>——这是框架重试判定的缺陷,
不是后端抖动;按工具集中在 {tool_items[0][0] if tool_items else "—"}
({tool_items[0][1]:,} 次),按接口 {ep_items[0][0].split("/")[-1] if ep_items else "—"}
占 {ep_items[0][1] / tot:.1%}。治理优先级:先修框架"成功后仍重发",再治该接口的
路径失效(业务错误 11410)。</p>"""
    return fig_to_b64(fig), text


# 工具错误归因的固定类目顺序与配色(与 tool_error_rate 归因目录 v1 对齐;
# unclassified 刻意用中性灰,提醒读者它是"目录没覆盖"而非"已识别的某类")。
_TOOL_ERR_ATTR_ORDER = (
    "model_misuse",
    "unclassified",
    "infrastructure_failure",
    "data_availability",
)
_TOOL_ERR_ATTR_COLORS = {
    "model_misuse": "#d32f2f",
    "unclassified": "#9e9e9e",
    "infrastructure_failure": "#1565c0",
    "data_availability": "#2e7d32",
}


def build_tool_error_attribution_stats(rows) -> dict:
    """从宽表 `tool_error_attribution` 聚合工具错误归因,供 3.3 节图表与文案。

    全部取自宽表单元格,零回源。返回:
      att            全量归因计数(by_attribution,所有有错行)
      bt             全量逐工具错误计数(by_tool,所有有错行)
      total          错误总次数
      logs_with_err  有工具错误的日志数
      terminal       工具调用终态总数(错误率分母,取自 tool_error_rate)
      presence       各工具"出现日志数"(失败率分母,取自 per_tool_call_latency_ms)
      err_logs       各工具"出错日志数"(失败率分子)
      per_tool_attr  逐工具归因矩阵——仅用"单工具失败"的日志推导(该日志 by_tool
                     恰 1 个工具时 by_attribution 可无歧义归给该工具);多工具混失败
                     的行不计入,故这是代理值,coverage_single 标注其覆盖的错误质量占比
      coverage_single 单工具推导覆盖的错误次数 / total
      storm_logs     错误次数>=10 的日志数;storm_share 其承担的错误占比
    """
    att: Counter = Counter()
    bt: Counter = Counter()
    presence: Counter = Counter()
    err_logs: Counter = Counter()
    per_tool_attr: dict = defaultdict(Counter)
    total = 0
    logs_with_err = 0
    terminal = 0
    single_mass = 0
    storm_logs = 0
    storm_mass = 0
    for r in rows:
        p = _j(r, "per_tool_call_latency_ms")
        if isinstance(p, list):
            for o in p:
                if isinstance(o, dict) and isinstance(o.get("tool_name"), str):
                    presence[o["tool_name"]] += 1
        te = _j(r, "tool_error_rate")
        if isinstance(te, list) and te and isinstance(te[0], dict):
            terminal += te[0].get("terminal_call_count", 0) or 0
        a = _j(r, "tool_error_attribution")
        if not (isinstance(a, list) and a and isinstance(a[0], dict)):
            continue
        logs_with_err += 1
        tools = [t for t in (a[0].get("by_tool") or []) if isinstance(t, dict)]
        attrs = [x for x in (a[0].get("by_attribution") or []) if isinstance(x, dict)]
        row_total = sum(int(t.get("count", 0)) for t in tools)
        total += row_total
        if row_total >= 10:
            storm_logs += 1
            storm_mass += row_total
        for t in tools:
            bt[str(t.get("name") or "?")] += int(t.get("count", 0))
            err_logs[str(t.get("name") or "?")] += 1
        for x in attrs:
            att[str(x.get("name") or "?")] += int(x.get("count", 0))
        if len(tools) == 1:
            name = str(tools[0].get("name") or "?")
            single_mass += int(tools[0].get("count", 0))
            for x in attrs:
                per_tool_attr[name][str(x.get("name") or "?")] += int(x.get("count", 0))
    return {
        "att": att,
        "bt": bt,
        "total": total,
        "logs_with_err": logs_with_err,
        "terminal": terminal,
        "presence": presence,
        "err_logs": err_logs,
        "per_tool_attr": per_tool_attr,
        "coverage_single": (single_mass / total) if total else 0.0,
        "storm_logs": storm_logs,
        "storm_share": (storm_mass / total) if total else 0.0,
    }


def chart_tool_error_attribution(rows) -> tuple[str, str, str]:
    """3.3 图一:归因分布 + 工具错误量 Top10 双栏;返回 (图, 分布文字, 洞察文字)。"""
    s = build_tool_error_attribution_stats(rows)
    att, bt, total = s["att"], s["bt"], s["total"]
    att_items = [(k, att.get(k, 0)) for k in _TOOL_ERR_ATTR_ORDER]
    tool_items = bt.most_common(10)

    row_in = 0.34
    rows_n = max(len(att_items), len(tool_items), 1)
    fig_h = rows_n * row_in + 1.5
    fig = plt.figure(figsize=(12.6, fig_h))
    top = 1 - 0.70 / fig_h

    def _panel(rect, items, colors, xlabel, title, fmt):
        height = len(items) * row_in / fig_h
        ax = fig.add_axes([rect[0], top - height, rect[1] - rect[0], height])
        y = list(range(len(items)))
        vals = [c for _, c in items]
        ax.barh(y, vals, color=colors, alpha=0.9, height=0.72, edgecolor="#455a64", linewidth=0.6)
        ax.set_yticks(y)
        ax.set_yticklabels([n for n, _ in items], fontsize=9.5)
        ax.invert_yaxis()
        ax.set_xlim(0, max(vals) * 1.5 if max(vals) else 1)
        ax.set_xlabel(xlabel, fontsize=10.5)
        ax.set_title(title, fontsize=12, pad=10)
        for yi, v in zip(y, vals):
            ax.text(v, yi, fmt(v), va="center", ha="left", fontsize=9.5)
        ax.tick_params(axis="x", labelsize=9.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    _panel(
        (0.16, 0.47),
        att_items,
        [_TOOL_ERR_ATTR_COLORS.get(k, "#757575") for k, _ in att_items],
        "错误次数",
        "归因分布(全量)",
        lambda v: f"  {v:,} ({v / total:.0%})" if total else f"  {v:,}",
    )
    _panel((0.64, 1.0), tool_items, "#8e24aa", "错误次数", f"工具错误量 Top{len(tool_items)}",
           lambda v: f"  {v:,} ({v / total:.0%})" if total else f"  {v:,}")

    top3 = sum(c for _, c in bt.most_common(3))
    top5 = sum(c for _, c in bt.most_common(5))
    dist = f"""<p>全量 <b>{total:,}</b> 次工具错误分布在 <b>{len(bt)}</b> 种工具上,
    有错误的日志 {s['logs_with_err']:,} / {len(rows):,} =
    <b>{s['logs_with_err'] / len(rows):.1%}</b>;微平均错误率
    <b>{total / s['terminal']:.2%}</b>(分母=工具调用终态 {s['terminal']:,})。
    归因四类:model_misuse {att.get('model_misuse', 0):,}
    ({att.get('model_misuse', 0) / total:.1%})、unclassified {att.get('unclassified', 0):,}
    ({att.get('unclassified', 0) / total:.1%})、infrastructure
    {att.get('infrastructure_failure', 0):,}、data_availability
    {att.get('data_availability', 0):,}。</p>"""
    insight = f"""<p><b>关键洞察一:错误高度集中,治理应按"量"派单。</b>Top1
    ({tool_items[0][0] if tool_items else '—'}) 占 {tool_items[0][1] / total:.1%}、Top3 占
    {top3 / total:.1%}、Top5 占 {top5 / total:.1%};错误数≥10 的"风暴日志"仅
    {s['storm_logs']:,} 条(占全量 {s['storm_logs'] / len(rows):.1%})却承担
    <b>{s['storm_share']:.1%}</b> 的错误——优先人工复查这 {s['storm_logs']:,} 条,
    比全量治理效率高一个量级。</p>
    <p><b>关键洞察二:{att.get('unclassified', 0) / total:.0%} 的 unclassified 表示归因目录
    v1 未命中这些错误文本。</b>unclassified 是"目录没匹配上"的定义性结果,不等于原因不明;
    补齐目录即可把这部分还原为可派单的类别,当前 model_misuse 占比因此可能被低估。
    右图(逐工具归因矩阵)显示未归类集中在 excel / cp / read / scheduler_task,
    而 write / grep / word 以 model_misuse 为主——两类工具的治理手段完全不同。</p>"""
    return fig_to_b64(fig), dist, insight


def chart_tool_error_rate_and_matrix(rows) -> tuple[str, str]:
    """3.3 图二:工具失败率 Top10 + 逐工具归因堆叠;返回 (图, 文字)。

    失败率 = 出错日志数 ÷ 出现该工具的日志数,与"错误量"是两个维度:量看治理优先级,
    率看"这个工具多容易坏"。逐工具归因矩阵仅用单工具失败日志推导(见
    build_tool_error_attribution_stats 的 coverage_single),多工具混失败行不计入。
    """
    s = build_tool_error_attribution_stats(rows)
    presence, err_logs, bt = s["presence"], s["err_logs"], s["bt"]
    per_tool_attr = s["per_tool_attr"]

    rate_items = []
    for tool, p in presence.items():
        if p < 50:
            continue
        e = err_logs.get(tool, 0)
        if e:
            rate_items.append((tool, e / p))
    rate_items.sort(key=lambda x: -x[1])
    rate_items = rate_items[:10]

    # 堆叠图取错误量 Top8,保证与"量"排名同序、便于交叉阅读
    stack_tools = [t for t, _ in bt.most_common(8)]

    row_in = 0.34
    rows_n = max(len(rate_items), len(stack_tools), 1)
    fig_h = rows_n * row_in + 1.5
    fig = plt.figure(figsize=(12.6, fig_h))
    top = 1 - 0.70 / fig_h

    # 左:失败率
    h1 = len(rate_items) * row_in / fig_h
    ax = fig.add_axes([0.16, top - h1, 0.31, h1])
    y = list(range(len(rate_items)))
    vals = [r for _, r in rate_items]
    ax.barh(y, vals, color="#f57c00", alpha=0.9, height=0.72, edgecolor="#455a64", linewidth=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels([n for n, _ in rate_items], fontsize=9.5)
    ax.invert_yaxis()
    ax.set_xlim(0, max(vals) * 1.4 if vals else 1)
    ax.set_xlabel("失败率(出错日志/出现日志)", fontsize=10)
    ax.set_title(f"工具失败率 Top{len(rate_items)}(出现≥50 日志)", fontsize=11.5, pad=10)
    for yi, v in zip(y, vals):
        ax.text(v, yi, f"  {v:.0%}", va="center", ha="left", fontsize=9.5)
    ax.tick_params(axis="x", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # 右:逐工具归因堆叠(单工具日志推导)
    h2 = len(stack_tools) * row_in / fig_h
    ax2 = fig.add_axes([0.62, top - h2, 0.36, h2])
    y2 = list(range(len(stack_tools)))
    left = [0.0] * len(stack_tools)
    for cat in _TOOL_ERR_ATTR_ORDER:
        widths = []
        for i, t in enumerate(stack_tools):
            tot_t = sum(per_tool_attr[t].values()) or 1
            widths.append(per_tool_attr[t].get(cat, 0) / tot_t)
        ax2.barh(y2, widths, left=left, color=_TOOL_ERR_ATTR_COLORS[cat], alpha=0.9,
                 height=0.72, edgecolor="#455a64", linewidth=0.6, label=cat)
        left = [a + b for a, b in zip(left, widths)]
    ax2.set_yticks(y2)
    ax2.set_yticklabels(stack_tools, fontsize=9.5)
    ax2.invert_yaxis()
    ax2.set_xlim(0, 1)
    ax2.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax2.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=9)
    ax2.set_xlabel("归因构成占比(单工具失败日志推导)", fontsize=10)
    ax2.set_title(f"逐工具归因矩阵 Top{len(stack_tools)}(按错误量)", fontsize=11.5, pad=10)
    # 图例放整张图下方横排:堆叠条本身是满宽的,放图内任何位置都会压住条体
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=4, frameon=False, fontsize=8.5)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    txt = f"""<p><b>"量"与"率"分歧本身是洞察。</b>grep / word 是"又常用又爱坏"
    (量、率双高);read 用量最大({presence.get('read', 0):,} 条日志)但失败率仅
    {err_logs.get('read', 0) / presence['read']:.1%},很稳;scheduler_task 失败率
    {err_logs.get('scheduler_task', 0) / presence['scheduler_task']:.1%} 却 0% 致死、
    Token 极小,属低危害噪声。三者应用不同策略:grep/word 修行为、read 不必动、
    scheduler_task 先补归因目录再谈治理。右图显示 excel / cp / read / scheduler_task
    的错误几乎全落在 unclassified(目录盲区),而 write / grep / word 以 model_misuse
    为主(可修的行为问题)。</p>
    <p class="note">逐工具归因矩阵由"单工具失败"日志推导,覆盖
    {s['coverage_single']:.1%} 的错误质量;多工具混失败的行未计入,故为代理值。
    by_tool 的错误总量({s['total']:,})是全量精确值。</p>"""
    return fig_to_b64(fig), txt


def build_llm_error_stats(rows):
    """从宽表聚合模型层错误:分类计数、模型错误数、模型调用数、是否有调用分母。"""
    classes: Counter = Counter()
    model_errors: Counter = Counter()
    model_calls: Counter = Counter()
    has_denominator = False
    for r in rows:
        obj = _j(r, "llm_error_count")
        if not isinstance(obj, list) or not obj or not isinstance(obj[0], dict):
            continue
        payload = obj[0]
        raw_classes = payload.get("error_classes")
        if isinstance(raw_classes, list):
            for item in raw_classes:
                if isinstance(item, dict) and isinstance(item.get("count"), (int, float)):
                    classes[str(item.get("class") or "unclassified")] += int(item["count"])
        raw_models = payload.get("error_models")
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, dict) and isinstance(item.get("count"), (int, float)):
                    model_errors[str(item.get("model") or "?")] += int(item["count"])
        raw_calls = payload.get("model_calls")
        if isinstance(raw_calls, list):
            # 键存在即代表评测器 1.3.0+ 口径,即使全部模型都无调用也说明分母可信
            has_denominator = True
            for item in raw_calls:
                if isinstance(item, dict) and isinstance(item.get("count"), (int, float)):
                    model_calls[str(item.get("model") or "?")] += int(item["count"])
    return classes, model_errors, model_calls, has_denominator


def _model_label(item, has_denominator: bool) -> str:
    """模型条目标注:优先给"错误数 ÷ 调用数 = 出错率",缺分母时如实标注。"""
    name, errors, calls = item
    if has_denominator:
        rate = f"{errors / calls:.2%}" if calls else "—"
        return f"{errors:,} / {calls:,} = {rate}"
    return f"{errors:,} 次(调用数待补)"


def _llm_error_tables(
    class_items, model_items, total_errors, has_denominator, extra_note: str = ""
):
    """分类与模型的数值表 + 口径注(numbers 与图一致,便于复制引用)。

    extra_note 由调用方注入与样本相关的读法说明(如「占比高 ≠ 稳定性风险」),
    避免本函数为了拿样本统计而多收一个 rows 参数。
    """
    table = '<table class="left"><tr><th>错误分类</th><th>出现次数</th><th>占全部错误</th></tr>'
    for name, count in class_items:
        ratio = f"{count / total_errors:.2%}" if total_errors else "—"
        table += f"<tr><td>{name}</td><td>{count:,}</td><td>{ratio}</td></tr>"
    table += f"<tr><td><b>合计</b></td><td><b>{total_errors:,}</b></td><td><b>100.00%</b></td></tr>"
    table += "</table>"

    heads = ["模型", "出错次数", "调用数", "出错率"]
    table += "<table class='left'><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr>"
    for _name, errors, calls in model_items:
        if has_denominator:
            rate = f"{errors / calls:.2%}" if calls else "—"
            table += (
                f"<tr><td>{_name}</td><td>{errors:,}</td><td>{calls:,}</td><td>{rate}</td></tr>"
            )
        else:
            table += f"<tr><td>{_name}</td><td>{errors:,}</td><td>待补</td><td>待补</td></tr>"
    table += "</table>"

    if has_denominator:
        note = (
            '<p class="note"><b>出错率口径</b>:该模型出错次数 ÷ 该模型调用次数。'
            "调用数 = 该模型在日志内产生的<b>唯一 assistant 消息数</b>(同一条消息的多次"
            "流式投影只计一次),与错误归因取自同一路由通道,因此两者可直接相除。"
            "分类占比以<b>全部模型层错误</b>为分母;模型出错率以<b>该模型自身调用数</b>为分母,"
            "两个占比不是同一量纲,不可横向比较。</p>"
        )
    else:
        note = (
            '<p class="note"><b>模型出错率暂不可得</b>:当前宽表的 `llm_error_count` 单元格'
            "为评测器 1.2.0 及以前形态,不含模型调用数(`total_call_count` / `model_calls`)"
            "——该字段随 1.3.0 引入。分子(出错次数)与分母(调用次数)必须取自同一样本,"
            "因此这里只列出错次数,不拿旧表硬算出错率。"
            "重跑宽表(全量 7,582 条日志)后本节自动补齐出错率,无需改脚本。</p>"
        )
    note += (
        '<p class="note">分类为<b>目录穷举</b>:`llm_error_class_v1` 的全部 '
        f"{len(class_items)} 个类目都在表内,零命中类同样列出(0 次 ≠ 该类不存在);"
        "未命中任何特征文本的错误归 `unclassified`,不猜测语义。</p>"
    )
    if extra_note:
        note += extra_note
    return table, note


# ---------------- HTML ----------------


def main() -> None:
    ap = argparse.ArgumentParser(description="生成指标宽表自包含 HTML 报告")
    ap.add_argument("--csv", type=Path, default=Path("data/processed/metric_table_full.csv"))
    ap.add_argument("--output", type=Path, default=Path("reports/metric_report.html"))
    ap.add_argument("--title", default="研究模式指标宽表分析报告")
    # 数据日期区间(必填):2026-09-24 需求方要求每份报告的表头都必须标明本批次
    # 日志的数据日期,否则读者无法判断这份统计对应哪段时间。宽表本身不存日期字段
    # (只有 user_id/session_id/log_id/mode + 指标列),无法从 CSV 自动推导,
    # 故由调用方按本次导出清单的日期范围显式传入。格式约定 `YYYYMMDD-YYYYMMDD`。
    ap.add_argument(
        "--data-range",
        required=True,
        help="本次统计覆盖的日志数据日期区间,格式 YYYYMMDD-YYYYMMDD(如 20260915-20260922)",
    )
    args = ap.parse_args()

    data_range = args.data_range.strip()
    if not data_range:
        ap.error("--data-range 不能为空;表头必须标明数据日期")
    # 宽松校验:只拦明显不是区间串的输入,不强制 8 位数字(调研批次可能带后缀)
    if not re.fullmatch(r"[0-9]{8}\s*[-~—]\s*[0-9]{8}", data_range):
        ap.error(
            f"--data-range 格式应为 YYYYMMDD-YYYYMMDD,收到 {data_range!r};"
            "如确需非常规区间请改脚本校验并在此说明理由"
        )
    data_range_disp = re.sub(r"\s*[-~—]\s*", "-", data_range)

    rows = load_rows(args.csv)
    n = len(rows)
    # 全样本未闭环率:2.3 死亡富集与第 4/5 节所有富集倍数的共同基线,
    # 只算一次,避免各节各自算导致倍数口径漂移。
    base_death_rate = sum(1 for r in rows if r.get("turn_completion_rate") == "0") / n if n else 0.0

    # 标量分位数(比率列按百分比展示,精确到万分之一即小数点后两位)
    def _fmt(v, typ, is_ratio):
        if v is None:
            return "-"
        if is_ratio:
            return f"{v:.2%}"
        # 整数值指标保持整数形态,不做小数位补齐
        return f"{v:,.0f}" if typ == "int" else f"{v:,.2f}"

    quant_rows = []
    for col, typ, is_ratio, cn_name, in_quant in SCALAR_COLS:
        if not in_quant:
            continue
        vals = []
        for r in rows:
            v = r.get(col)
            if not v:
                continue
            try:
                vals.append(float(v))
            except ValueError:
                pass
        if not vals:
            continue
        row = {
            "指标": cn_name,
            "非空": len(vals),
            "p50": _fmt(pct(vals, 0.5), typ, is_ratio),
            "p80": _fmt(pct(vals, 0.8), typ, is_ratio),
            "p90": _fmt(pct(vals, 0.9), typ, is_ratio),
            "p95": _fmt(pct(vals, 0.95), typ, is_ratio),
            "p99": _fmt(pct(vals, 0.99), typ, is_ratio),
            "均值": _fmt(statistics.mean(vals), typ, is_ratio),
        }
        quant_rows.append(row)

    # 逐工具聚合(保留 agg dict 供图表 + 排序后的 stat_rows 供表格)
    # stat_rows 单元:(工具名, 出现日志数, p50, p80, p90, p95, p99, 均值)
    # 出现日志数取 presence 口径(日志级去重,不要求取值可计算),与第 3.2 节一致
    tool_agg = {}
    tool_presence = {}
    tool_tables = {}
    for col, key, label in PER_TOOL_COLS:
        agg = collect_per_tool(rows, col, key)
        presence = collect_tool_presence(rows, col)
        stat_rows = []
        for name, vals in agg.items():
            row_pcts = [pct(vals, p) for p in (0.5, 0.8, 0.9, 0.95, 0.99)]
            stat_rows.append((name, presence.get(name, 0), *row_pcts, statistics.mean(vals)))
        stat_rows.sort(key=lambda x: -x[-1])
        tool_agg[col] = agg
        tool_presence[col] = presence
        tool_tables[col] = (stat_rows, label)

    # 稳定性维度信号(标签/日志数/定义举例),按频次降序;图表与表格共用
    stab_signals = build_stability_signals(rows)

    # ---- 洞察计算(第 4/5 节) ----
    dead = [r for r in rows if r.get("turn_completion_rate") == "0"]
    # 高危日志口径(2026-09-24 二次修订): 闭环 ∧ (API业务错误数 be>=3 ∨ 工具错误数 te>10)。
    # be>=3 由富集分析确认对高消耗(3.6x)与死亡(2.4x)双重富集最强;新口径 rate 对死亡
    # 无判别力(1.2x),且 be>=3∧rate 反而削弱信号,故弃用旧"压缩>0∨重试>0.6"。
    # te>10 为同日追加:该分支单独多收 17 条闭环日志(中位 19.1M Token = 全样本 11.8x),
    # 其中 15 条不同时满足 be>=3,属"错得多且特别贵"的另一类消耗主体,原口径完全漏掉。
    # 两条件按 log_id 取并集(同一日志只计一次),与 bad case sheet 口径一致。
    high = [
        r
        for r in rows
        if r.get("turn_completion_rate") == "1"
        and (
            jnum(r, "api_retry_rate", "api_business_error_count") >= HIGH_BE_THRESHOLD
            or jnum(r, "tool_error_rate", "error_call_count") > HIGH_TOOL_ERROR_THRESHOLD
        )
    ]
    # 高危构成拆分:用于 4.2 说明"两个条件各自收到什么",避免读者以为只有一类。
    high_only_be = [
        r
        for r in high
        if jnum(r, "api_retry_rate", "api_business_error_count") >= HIGH_BE_THRESHOLD
        and jnum(r, "tool_error_rate", "error_call_count") <= HIGH_TOOL_ERROR_THRESHOLD
    ]
    high_only_te = [
        r
        for r in high
        if jnum(r, "api_retry_rate", "api_business_error_count") < HIGH_BE_THRESHOLD
    ]
    high_both = len(high) - len(high_only_be) - len(high_only_te)
    high_be = len(high)
    dead_te = sum(1 for r in dead if jnum(r, "tool_error_rate", "error_call_count") > 0)
    dead_none = sum(
        1
        for r in dead
        if jnum(r, "tool_error_rate", "error_call_count") == 0
        and jnum(r, "llm_error_count", "llm_error_count") == 0
    )
    total_tok = sum(
        float(r["total_token_consumption"]) for r in rows if r.get("total_token_consumption")
    )
    dead_tok = sum(
        float(r["total_token_consumption"]) for r in dead if r.get("total_token_consumption")
    )
    high_tok = sum(
        float(r["total_token_consumption"]) for r in high if r.get("total_token_consumption")
    )

    def med_tok(sub):
        v = sorted(
            float(r["total_token_consumption"]) for r in sub if r.get("total_token_consumption")
        )
        return v[len(v) // 2] if v else 0

    _median_all = med_tok(rows)

    # 框架层重试"前置成功仍重发"占比(5.2 文案用),与 3.5 同源
    _rates, _zero, _rtool, _rep, _ras, _tot = build_api_retry_stats(rows)
    ras_pct = f"{_ras / _tot:.1%}" if _tot else "—"
    _top_rep = _rep.most_common(1)[0] if _rep else ("—", 0)
    top_rep_name = str(_top_rep[0]).split("/")[-1]
    top_rep_cnt = _top_rep[1]

    # ---- 迭代建议所需的统计(相关性 / 分位数 / 每次调用成本) ----
    def _corr(xs, ys):
        if len(xs) < 2:
            return 0.0
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs)
        dx = statistics.pstdev(xs)
        dy = statistics.pstdev(ys)
        return cov / (dx * dy) if dx and dy else 0.0

    # 相关性只在"真的调过工具"的日志上算:调用数为 0 的日志(无工具调用)会把
    # 原点附近塞满点、人为拉高相关系数,混进来等于把"没调过"当成"调得少"。
    # 注意宽表里 "0" 是非空字符串,`r.get(...)` 判真挡不住,必须显式比较数值。
    def _calls(r):
        try:
            return int(r.get("tool_calls_count") or 0)
        except ValueError:
            return 0

    tc_tok_pairs = [
        (_calls(r), float(r["total_token_consumption"]))
        for r in rows
        if _calls(r) > 0 and r.get("total_token_consumption")
    ]
    corr_tc_tok = _corr([p[0] for p in tc_tok_pairs], [p[1] for p in tc_tok_pairs])
    total_calls = sum(int(r["tool_calls_count"]) for r in rows if r.get("tool_calls_count"))
    total_tokens = sum(
        float(r["total_token_consumption"]) for r in rows if r.get("total_token_consumption")
    )
    per_call_token = total_tokens / total_calls if total_calls else 0
    total_lat = sum(
        float(r["end_to_end_latency_ms"]) for r in rows if r.get("end_to_end_latency_ms")
    )
    per_call_lat_s = total_lat / total_calls / 1000 if total_calls else 0

    # 出错恢复率与 task 子 Agent 委派时延(第 5 节建议文案用,原本是硬编码常量)
    jnum_recovered = sum(1 for r in rows if jnum(r, "error_recovery_rate", "error_recovered") == 1)
    total_had_error = sum(1 for r in rows if jnum(r, "error_recovery_rate", "had_error") == 1)
    recovered_pct = f"{jnum_recovered / total_had_error:.0%}" if total_had_error else "—"
    task_vals = tool_agg["per_tool_call_latency_ms"].get("task", [])
    task_mean_s = statistics.mean(task_vals) / 1000 if task_vals else 0.0
    task_total_h = sum(task_vals) / 3.6e6

    # 逐工具耗时排名摘要(按均值)
    lat_agg = tool_agg["per_tool_call_latency_ms"]
    lat_pres = tool_presence["per_tool_call_latency_ms"]
    lat_rank = sorted(((n, statistics.mean(v)) for n, v in lat_agg.items()), key=lambda x: -x[1])

    def _tool_rows(rank, presence, unit, nd=0):
        out = []
        for name, mean in rank[:10]:
            out.append(
                f"<tr><td>{name}</td><td>{presence.get(name, 0):,}</td>"
                f"<td>{mean / unit:,.{nd}f}</td></tr>"
            )
        return "".join(out)

    def _tool_rows_note(rank, presence, top=4):
        """4.3 注:把表内前几名连同数值写进正文,数字实时取,不留硬编码。

        同时标注小样本项:排名按均值,一条只出现过两三次的工具其均值没有代表性,
        不点明会让读者把噪声当成"重工具"。
        """
        parts = []
        for name, mean in rank[:top]:
            n = presence.get(name, 0)
            tag = f"(仅 {n} 条日志)" if n < 20 else ""
            parts.append(f"{name} {mean / 1000:.1f}s{tag}")
        return "、".join(parts) if parts else "—"

    def _tool_rows_inline(presence, top=7):
        """4.4 正文:把最高频工具连同出现日志数写进句子,数字实时取。"""
        parts = [f"{name}({cnt:,})" for name, cnt in presence.most_common(top)]
        return "、".join(parts) if parts else "—"

    # ---- 第 4/5 节新增洞察所需的统计(全部实时计算,禁止写死数字) ----
    # 步数分档风险曲线:报告最重要的可操作杠杆。步数是唯一能在执行中廉价观测、
    # 且与"跑不完"强相关的量,因此按档位给出死亡率与成本占比,供 5.3 定刹车点。
    STEP_BANDS = [(0, 9), (10, 29), (30, 59), (60, 99), (100, 199), (200, None)]

    def _band_label(lo, hi):
        return f"{lo}-{hi}" if hi is not None else f"{lo}+"

    step_bands = []
    for lo, hi in STEP_BANDS:
        sub = [r for r in rows if lo <= _calls(r) and (hi is None or _calls(r) <= hi)]
        if not sub:
            continue
        sub_dead = [r for r in sub if r.get("turn_completion_rate") == "0"]
        sub_tok = sum(
            float(r["total_token_consumption"]) for r in sub if r.get("total_token_consumption")
        )
        step_bands.append(
            {
                "label": _band_label(lo, hi),
                "n": len(sub),
                "share": len(sub) / n,
                "dead": len(sub_dead),
                "dead_rate": len(sub_dead) / len(sub),
                "tok_share": sub_tok / total_tok if total_tok else 0.0,
            }
        )
    # 头部档(≥100 步)是 5.3 建议的刹车区间
    head_bands = [b for b in step_bands if b["label"].endswith("+") or b["label"] == "100-199"]
    head_n = sum(b["n"] for b in head_bands)
    head_share = head_n / n if n else 0.0
    head_tok_share = sum(b["tok_share"] for b in head_bands)
    head_dead = sum(b["dead"] for b in head_bands)
    head_dead_rate = head_dead / head_n if head_n else 0.0

    # 小日志(<30 步)几乎不会失败,成本占比也低——刹车只该装在长尾上。
    # 注意不能拿 step_bands[0](仅 0-9 步)冒充 <30:两者死亡率差一个量级。
    def _steps(r):
        return _calls(r)

    short_rows = [r for r in rows if _steps(r) < 30]
    short_dead = sum(1 for r in short_rows if r.get("turn_completion_rate") == "0")

    # 压缩闸门:压缩是死亡判别力最强的信号(富集远高于其它),但样本量小
    comp_rows = [r for r in rows if int(r.get("compaction_event_count") or 0) > 0]
    comp_dead = sum(1 for r in comp_rows if r.get("turn_completion_rate") == "0")
    comp_rate = comp_dead / len(comp_rows) if comp_rows else 0.0

    # 模型层错误的实际危害:分类占比高不等于有稳定性风险,须与死亡富集区分
    llm_dead = sum(
        1
        for r in rows
        if jnum(r, "llm_error_count", "llm_error_count") > 0
        and r.get("turn_completion_rate") == "0"
    )
    llm_hit = sum(1 for r in rows if jnum(r, "llm_error_count", "llm_error_count") > 0)
    llm_dead_rate = llm_dead / llm_hit if llm_hit else 0.0
    llm_enrich = llm_dead_rate / base_death_rate if base_death_rate else 0.0
    # 模型错误的第一大分类(报告 3.4 的主导类目),用于说明"占比高 ≠ 危险"
    _llm_classes, _, _, _ = build_llm_error_stats(rows)
    _top_llm_class = _llm_classes.most_common(1)[0] if _llm_classes else ("—", 0)
    _llm_total = sum(_llm_classes.values())

    # 工具错误归因缺口:unclassified 占比高说明归因目录覆盖不足,是工具侧改进项
    te_total = sum(jnum(r, "tool_error_rate", "error_call_count") for r in rows)
    te_uncls = sum(jnum(r, "tool_error_rate", "unclassified_error_count") for r in rows)
    te_misuse = sum(jnum(r, "tool_error_rate", "model_misuse_error_count") for r in rows)

    # 3.3 节工具错误归因:第 5 节建议直接引用这里的集中度与失败率,单一事实来源
    te_stats = build_tool_error_attribution_stats(rows)
    _te_bt = te_stats["bt"]
    _te_top = _te_bt.most_common(5)
    te_top_tool, te_top_cnt = _te_top[0] if _te_top else ("—", 0)
    te_top_share = _ratio(te_top_cnt, te_stats["total"])
    te_top5_share = _ratio(sum(c for _, c in _te_top), te_stats["total"])
    # 失败率(出错日志数 ÷ 出现日志数)Top3,只取样本量够的工具
    _te_rate = []
    for _t, _p in te_stats["presence"].items():
        if _p < 50:
            continue
        _e = te_stats["err_logs"].get(_t, 0)
        if _e:
            _te_rate.append((_t, _e / _p))
    _te_rate.sort(key=lambda x: -x[1])
    te_rate_note = "、".join(f"{t} {r:.0%}" for t, r in _te_rate[:3]) or "—"
    # 归因最集中的工具(错误量 Top3 各自的首要归因),用于说明两类工具病灶不同
    _te_mix = []
    for _t, _c in _te_top[:3]:
        _d = te_stats["per_tool_attr"].get(_t) or {}
        _tot_t = sum(_d.values()) or 1
        _top_attr = max(_d.items(), key=lambda kv: kv[1])[0] if _d else "—"
        _te_mix.append((_t, _top_attr, _d.get(_top_attr, 0) / _tot_t))
    # 错误最集中的"风暴日志"(单日志错误数≥10)数量与占比,供 5.1/5.2 引用
    _te_storm_logs = te_stats["storm_logs"]
    _te_storm_share = te_stats["storm_share"]

    # 死亡富集最高的工具:5.1 的加固清单按实测富集选,不写死工具名
    _dead_set = {r["log_id"] for r in dead}
    _tool_dead: Counter = Counter()
    _tool_all: Counter = Counter()
    for r in rows:
        names = {
            o.get("tool_name")
            for o in (_j(r, "per_tool_call_latency_ms") or [])
            if isinstance(o, dict) and isinstance(o.get("tool_name"), str)
        }
        for name in names:
            _tool_all[name] += 1
            if r["log_id"] in _dead_set:
                _tool_dead[name] += 1
    # 只对有足够样本量的工具算富集:小样本的比例噪声会让排名失去意义。
    # 门槛取 100(本样本约 1.3% 的日志数):15~100 之间的工具富集倍数虽高,
    # 但多数只有一二十条命中,一两条死亡就能把倍数推到 10x 以上,不可派单。
    _MIN_TOOL_HITS = 100
    risky_tools = []
    for name, hits in _tool_all.items():
        if hits < _MIN_TOOL_HITS:
            continue
        rate = _tool_dead.get(name, 0) / hits
        risky_tools.append((name, hits, rate, rate / base_death_rate if base_death_rate else 0.0))
    risky_tools.sort(key=lambda x: -x[3])
    top_risky = risky_tools[:3]
    top_risk_name = top_risky[0][0] if top_risky else "—"
    top_risk_enrich = top_risky[0][3] if top_risky else 0.0
    top_risk_hits = top_risky[0][1] if top_risky else 0
    risk_tool_list = "、".join(t[0] for t in top_risky) or "—"

    # be≥3 组(第 4.2 节高危口径)承载的框架重试与业务错误(占比在 insight 段计算)
    _be_err = sum(jnum(r, "api_retry_rate", "api_business_error_count") for r in high)
    _all_err = sum(jnum(r, "api_retry_rate", "api_business_error_count") for r in rows)
    _be_retry = sum(jnum(r, "api_retry_rate", "api_retry_count") for r in high)

    # 缓存已接近饱和:再压缓存收益有限,成本杠杆应转向步数与上下文体积
    cache_vals = [
        float(r["cache_input_token_ratio"]) for r in rows if r.get("cache_input_token_ratio")
    ]
    cache_med = statistics.median(cache_vals) if cache_vals else 0.0

    # 死亡日志的"沉没成本"结构:按步数分档看 Token,定位最贵的那批失败
    dead_head = [r for r in dead if _calls(r) >= 100]
    dead_head_tok = sum(
        float(r["total_token_consumption"]) for r in dead_head if r.get("total_token_consumption")
    )
    dead_tok_share = dead_head_tok / dead_tok if dead_tok else 0.0
    # 无信号死亡日志的步数中位(4.1 用来说明它们不是"一开头就崩")
    dead_none_rows = [
        r
        for r in dead
        if jnum(r, "tool_error_rate", "error_call_count") == 0
        and jnum(r, "llm_error_count", "llm_error_count") == 0
    ]
    dead_none_med_steps = (
        statistics.median([_calls(r) for r in dead_none_rows]) if dead_none_rows else 0
    )
    dead_med_steps = statistics.median([_calls(r) for r in dead]) if dead else 0

    # 步数分档风险曲线表:5.3 定刹车点的依据,同时也是 4.1 成本结构的解释
    step_band_html = (
        '<table class="left"><tr><th>工具调用步数</th><th>日志数</th><th>日志占比</th>'
        "<th>Token 占比</th><th>未闭环</th><th>未闭环率</th></tr>"
    )
    for b in step_bands:
        step_band_html += (
            f"<tr><td>{b['label']} 步</td><td>{b['n']:,}</td><td>{b['share']:.1%}</td>"
            f"<td>{b['tok_share']:.1%}</td><td>{b['dead']:,}</td>"
            f"<td>{b['dead_rate']:.1%}</td></tr>"
        )
    step_band_html += "</table>"
    last_band = step_bands[-1] if step_bands else {"dead_rate": 0.0, "label": "—"}
    last_band_dead_rate = last_band["dead_rate"]
    # <30 步的低危档:4.5 与 5.3 都要引用,避免两处各算一遍口径漂移
    short_n = len(short_rows)
    short_dead_rate = _ratio(short_dead, short_n)
    # 4.2 集中度:be≥3 组承接的框架重试 / 业务错误占比(live 计算)
    be_retry_share = _ratio(_be_retry, _tot)
    be_err_share = _ratio(_be_err, _all_err)

    insight_html = f"""
<div class="insight">
<h3>4.1 故障主体:死亡日志(未闭环)</h3>
<p>全量 {n:,} 条日志中 <b>{len(dead)} 条({len(dead) / n:.1%})未闭环</b>。它们是"砸在手上"的日志——花掉 Token 却没有产出。</p>
<ul>
<li><b>归因结构</b>:{dead_te / len(dead):.0%} 死于工具错误({dead_te}/{len(dead)})、{dead_none / len(dead):.0%} 无任何错误信号({dead_none}/{len(dead)})、仅 {sum(1 for r in dead if jnum(r, "llm_error_count", "llm_error_count") > 0) / len(dead):.1%} 有模型层错误。</li>
<li><b>成本</b>:死亡日志中位单条 {med_tok(dead) / 1e6:.0f}M Token,合计吃掉 <b>{dead_tok / total_tok:.1%}</b> 的总 Token({dead_tok / 1e9:.2f}B)。其中 <b>{len(dead_head)} 条</b>跑到 100 步以上才断,单这 {len(dead_head) / len(dead):.0%} 就占了死亡日志总 Token 的 <b>{dead_tok_share:.1%}</b>——失败的成本几乎全压在"跑得很久之后才断"的长日志上。</li>
<li><b>无信号 {dead_none / len(dead):.0%} 最可疑</b>:无工具错误、无模型错误、重试正常却未闭环。它们的中位步数 {dead_none_med_steps:.0f} 步(死亡日志整体中位 {dead_med_steps:.0f} 步),不像"一开头就崩",更像跑到中途被切断;但日志里没写原因,只能靠人工复查。</li>
</ul>

<h3>4.2 消耗主体:高危日志(有产出但消耗严重)</h3>
<p>闭环且命中 <b>业务错误数 be≥{HIGH_BE_THRESHOLD}</b> 或 <b>工具错误数 te&gt;{HIGH_TOOL_ERROR_THRESHOLD}</b> 的日志共 <b>{high_be} 条</b>。它们是"磨了很久才出来"的日志。</p>
<ul>
<li><b>成本富集</b>:高危日志中位单条 {med_tok(high) / 1e6:.1f}M Token,是全局中位的 <b>{med_tok(high) / _median_all:.1f}x</b>,{high_be / n:.1%} 的日志数吃掉 {high_tok / total_tok:.1%} 的总 Token。</li>
<li><b>两个条件各收到什么</b>:仅 be≥{HIGH_BE_THRESHOLD} 的 {len(high_only_be)} 条(反复撞同一接口/路径)、仅 te&gt;{HIGH_TOOL_ERROR_THRESHOLD} 的 {len(high_only_te)} 条(坏在多个不同工具上)、两者同时命中 {high_both} 条。后一类只占 {len(high_only_te) / n:.1%} 的日志数但中位单条 {med_tok(high_only_te) / 1e6:.1f}M Token,单看工具错误数就能捞出这批原口径漏掉的贵日志。</li>
<li><b>为何是这两个条件</b>:富集分析显示 be≥{HIGH_BE_THRESHOLD} 对高消耗(3.6x)与死亡(2.4x)双重富集最强;而重试率对死亡仅 1.2x、且 be≥{HIGH_BE_THRESHOLD}∧rate 反而把死亡富集打到 0.4x,故率不宜作门槛(详见 3.5)。te&gt;{HIGH_TOOL_ERROR_THRESHOLD} 补的是"错误分散在多个工具、但每条都不重"的场景,be≥{HIGH_BE_THRESHOLD} 抓不到。</li>
<li><b>与死亡日志几乎不重叠</b>:两者是独立的两类问题——一个"坏了",一个"贵了",打捞策略应分开。</li>
</ul>

<h3>4.3 工具侧热点</h3>
<p>单次调用最贵的工具(平均耗时)集中在<b>子 Agent 委派</b>与<b>长文档抽取</b>:</p>
<table><tr><th>工具</th><th>出现日志数</th><th>平均耗时(秒)</th></tr>
{_tool_rows(lat_rank, lat_pres, 1000, 1)}</table>
<p class="note">上表前几名依次为 {_tool_rows_note(lat_rank, lat_pres)}——长尾时延由这几个"重工具"贡献。</p>

<h3>4.4 工具使用频率</h3>
<p>最高频工具全是<b>文件/检索类基础操作</b>:{_tool_rows_inline(lat_pres)}。工具面广({len(lat_rank)} 种),但成本高度集中在少数重工具。</p>
<p class="note">本节与第 3.2 节的"频次"均为<b>出现该工具的日志数</b>(一条日志里调用 10 次 read 只计 1),
不是全样本调用总次数——两者口径不同,不可与调用数混比。</p>

<h3>4.5 步数分档:失败与成本的共同杠杆</h3>
<p>把日志按"工具调用步数"分档后,风险曲线非常干净——<b>步数越长,越容易跑不完,也越贵</b>:</p>
{step_band_html}
<p class="note">步数是少数能在<b>执行过程中廉价观测</b>、又与失败强相关的量:30 步以内几乎不会失败,
而 200 步以上有 <b>{last_band_dead_rate:.0%}</b> 跑不完。因此它是 5.3 建议的刹车依据,
而不是事后归因指标。</p>
</div>"""

    reco_html = f"""
<div class="reco">
<p>以下建议按<b>先止血、再降本、后提效</b>的顺序排列，每条都对应本样本的实测数字，不写没有数据支撑的空话。</p>

<h3>5.1 止血：先把"没跑完就断"的日志降下来</h3>
<p>全量 {n:,} 条日志里，<b>{len(dead)} 条（{len(dead) / n:.1%}）没跑到终点</b>：Token 已经花掉，答案却没交付。这是用户感受最直接的一类问题，先修。</p>
<ul>
<li><b>工具一报错，日志就停（占死亡日志 {dead_te / len(dead):.0%}）</b>：{dead_te} 条死在工具报错上。工具报错本不必致命——出过错的 {total_had_error:,} 条日志里有 {recovered_pct} 最终仍交付了。做法：按实测死亡富集排序加固重工具，当前最该加固的是 <b>{risk_tool_list}</b>（{top_risk_name} 在 {top_risk_hits:,} 条日志里出现过，死亡率是全局基线的 {top_risk_enrich:.1f} 倍）；给它们加上"报错后自动换一种方式重试或降级返回"，别让一次失败直接终止整条日志。</li>
<li><b>没报错却也断了（占死亡日志 {dead_none / len(dead):.0%}）</b>：{dead_none} 条既没有工具错、也没有模型错，就是没跑完。这通常是被超时切断、用户手动停止或进程被杀，但日志里没写原因，所以查不到。做法：先在日志里补一个"中断原因"字段，让这类问题从"看不见"变成"可统计"，才谈得上修。</li>
</ul>

<h3>5.2 降本：管住"跑完了但特别贵"的日志</h3>
<p>另一类问题日志是<b>跑完了、但过程中反复出错</b>（闭环且命中业务错误 ≥{HIGH_BE_THRESHOLD} 次或工具错误 &gt;{HIGH_TOOL_ERROR_THRESHOLD} 次）的 {high_be} 条：只占 {high_be / n:.1%} 的日志数，却吃掉 {high_tok / total_tok:.1%} 的总 Token，并承接全样本 {be_retry_share:.1%} 的框架层重复与 {be_err_share:.1%} 的业务错误。它和 5.1 是两回事，分开治。</p>
<ul>
<li><b>对同一个死路径反复问</b>：这类日志撞的墙集中在接口 {top_rep_name}（框架层重复 {top_rep_cnt:,} 次，见 3.5），典型情况是拿一个已删除的路径反复查询（业务错误码 11410）。做法：工具返回里直接写明"该路径不存在，无需重试"，或提示模型"同一报错连出现 2 次就换思路"，避免对死路径空转。</li>
<li><b>框架自己在重复发请求</b>：框架层重复请求共 {_tot:,} 次，其中 {ras_pct} 是"上一次明明已经成功，框架又原样发了一遍"（见 3.5）。这是框架重试判断的缺陷，不是模型问题，修框架侧的判定就能直接省下这部分调用。</li>
<li><b>先修最高频的坏工具，别平均用力</b>：全样本 {te_stats["total"]:,} 次工具错误里，单个 <b>{te_top_tool}</b> 就占 <b>{te_top_share:.1%}</b>，前五名工具合计 {te_top5_share:.1%}（见 3.3）；按失败率看最易坏的是 {te_rate_note}。做法：把改造资源先压到这几个工具上——它们的错误多属模型用法/参数问题（{_te_mix[0][0] if _te_mix else '—'} 的错误 {_te_mix[0][2]:.0%} 是 {_te_mix[0][1] if _te_mix else '—'}），改提示词与参数约束即可见效，不必等框架层重构。</li>
<li><b>风暴日志优先人工复查</b>：错误数≥10 的日志仅 {_te_storm_logs:,} 条（占全量 {_te_storm_logs / n:.1%}），却承担全样本 <b>{_te_storm_share:.1%}</b> 的工具错误——它们是"对同一面墙反复撞"的重灾区。先看这 {_te_storm_logs:,} 条，比全量铺开排查效率高一个量级。</li>
<li><b>把工具错误归因补齐</b>：全样本 {te_total:,} 次工具错误里，<b>{te_uncls:,} 次（{_ratio(te_uncls, te_total):.0%}）落在 unclassified</b>，只有 {_ratio(te_misuse, te_total):.0%} 被明确归到模型误用。归因不明的错误无法派单修复，建议先扩充归因目录覆盖、把 unclassified 占比压下去——这是工具侧所有改进的前置条件。</li>
<li><b>上下文越滚越大</b>：本样本平均每次工具调用摊 <b>{per_call_token / 1e6:.1f}M Token</b>，调用步数与总 Token 的相关性达 {corr_tc_tok:.2f}——调得越多，每轮要重读的历史就越长。做法：大的工具结果不要整段塞回上下文，改为写进工作区，上下文里只留一句摘要加编号，需要时再按编号读取；上下文减半，循环成本近似减半。</li>
<li><b>子任务在排队</b>：task（委派子 Agent）平均单次 {task_mean_s:.0f} 秒，累计约 {task_total_h:.0f} 小时，是时延里最大的一块。互不依赖的子任务应当同时派发、并行执行，而不是一个做完再做下一个；在"每次调用约 {per_call_lat_s:.0f} 秒"的串行结构下，并行是唯一能把时延降一个量级的办法。</li>
</ul>

<h3>5.3 提效：给长日志装一个"刹车"，别让它一路跑到底</h3>
<p>前两层分别治"断掉的"和"贵的"。这一层治的是两者的<b>共同上游</b>——日志一旦跑长，既容易断、又必然贵。4.5 的分档已经显示，风险几乎全部来自长尾：</p>
<ul>
<li><b>步数是最便宜的刹车信号</b>：≥{STEP_BANDS[-2][0]} 步的日志占 {head_share:.1%}，却吃掉 <b>{head_tok_share:.1%}</b> 的 Token，其中 <b>{head_dead_rate:.0%} 跑不完</b>；而 &lt;30 步的日志未闭环率仅 {short_dead_rate:.1%}（{short_n:,} 条）。做法：在执行侧设一个软阈值（比如超过 {STEP_BANDS[-2][0]} 步），触发一次<b>强制收敛</b>——让模型停下来交付当前可得的最好答案，或明确回答"现有信息不足以回答"，而不是继续无限追问。这同时压缩 4.1 里那批跑到 100 步以上才断的沉没成本（当前占死亡日志总 Token 的 {dead_tok_share:.1%}）。</li>
<li><b>压缩是最后的警告灯</b>：触发过自动压缩的日志只有 {len(comp_rows)} 条，但其中 <b>{comp_rate:.0%} 最终未闭环</b>，远高于全样本基线 {base_death_rate:.1%}。压缩意味着上下文已撑到必须裁剪，继续跑失败概率陡增。做法：把"发生自动压缩"当升级信号而非后台静默行为——压缩后强制重述目标与当前进度，或直接进入收敛流程。</li>
<li><b>模型层错误不必单独排期</b>：占全部模型错误 {_top_llm_class[1] / _llm_total:.0%} 的头号类目是 {_top_llm_class[0]}，但出过模型错误的 {llm_hit:,} 条日志未闭环率只有 {llm_dead_rate:.1%}（基线 {base_death_rate:.1%}，富集 {llm_enrich:.1f}x，<b>低于</b>基线）——框架重试已经把这类错误兜住了。它更接近成本项（重试多花的 Token）而非稳定性风险，不应占用止血资源。</li>
<li><b>缓存已接近饱和，别再指望它降本</b>：缓存占输入 Token 的中位数已达 {cache_med:.1%}，继续压缓存命中的空间有限；降本的主要空间在<b>减少步数与上下文体积</b>。</li>
</ul>

<p class="note">优先级：5.1（日志跑不完，用户最直接感受到）&gt; 5.2（跑得完但太贵，影响成本）&gt; 5.3（长日志刹车，同时改善前两者）。每次模型或工具变更后重跑本报告，盯住四个回归数字：未闭环日志数、高危日志数、工具错误率、工具错误 unclassified 占比。</p>
</div>"""

    # 生成图表
    charts = {
        "percentile_bars": chart_percentile_bars(rows),
        "tool_latency_rank": chart_tool_rank(
            tool_agg["per_tool_call_latency_ms"],
            tool_presence["per_tool_call_latency_ms"],
            "工具平均单调用耗时",
            "耗时(ms)",
            n,
        ),
        "tool_token_rank": chart_tool_rank(
            tool_agg["per_tool_call_tokens"],
            tool_presence["per_tool_call_tokens"],
            "工具平均单调用 Token",
            "Token",
            n,
        ),
        "tool_freq_rank": chart_tool_freq(tool_presence["per_tool_call_latency_ms"], n),
        "stability_dist": chart_stability_dist(stab_signals, n),
    }
    # 模型层错误:分类图 + 模型出错率图 + 两张数值表 + 口径注(一次性返回,避免口径漂移)。
    # 额外注复用上面已算的 llm_hit / llm_dead / llm_enrich:分类占比高 ≠ 稳定性风险,
    # 读者最容易把"错误最多"误当成"最该先修",必须显式点明。
    llm_extra_note = (
        '<p class="note"><b>读法提醒:占比高 ≠ 稳定性风险高。</b>'
        f"出过模型层错误的 {llm_hit:,} 条日志中,只有 {llm_dead:,} 条未闭环"
        f"({llm_dead_rate:.1%}),而未闭环基线是 {base_death_rate:.1%}"
        f"(富集 {llm_enrich:.1f}x,低于基线)。也就是说本节的高频类目"
        "主要反映<b>成本</b>(错误后重试多花的 Token),已由框架重试兜住,"
        "不构成'日志跑不完'的主因。"
        "这与分类出现次数是两回事,不要按错误数排修复优先级。</p>"
    )
    llm_error_img, llm_error_table, llm_error_note = chart_llm_error(rows, llm_extra_note)
    # API 重试(fingerprint_dup_v2):归因双栏图 + 洞察文字
    api_retry_img, api_retry_text = chart_api_retry(rows)
    # 工具错误归因(3.3):分布/量双栏图 + 失败率/归因矩阵图 + 各自文字
    te_attr_img, te_dist_txt, te_insight_txt = chart_tool_error_attribution(rows)
    te_matrix_img, te_matrix_txt = chart_tool_error_rate_and_matrix(rows)

    # HTML 组装
    quant_html = "<table><tr>" + "".join(f"<th>{k}</th>" for k in quant_rows[0]) + "</tr>"
    for row in quant_rows:
        quant_html += "<tr>" + "".join(f"<td>{row[k]}</td>" for k in row) + "</tr>"
    quant_html += "</table>"

    def tool_table_html(stat_rows, label):
        """逐工具表:出现日志数 + p50/p80/p90/p95/p99 + 均值。

        2026-09-23 需求方确认列集:只保留这六个统计字段,移除原「中位/最大」列
        (中位即 p50,与最大同属冗余展示)。
        """
        heads = ["工具", "出现日志数", "p50", "p80", "p90", "p95", "p99", "均值"]
        h = "<table><tr>" + "".join(f"<th>{x}</th>" for x in heads) + "</tr>"
        for name, freq, *rest in stat_rows[:25]:
            nums = "".join(f"<td>{v:,.0f}</td>" for v in rest)
            h += f"<tr><td>{name}</td><td>{freq:,}</td>{nums}</tr>"
        return h + "</table>"

    # 频次口径必须显式注明:出现日志数 ≠ 调用次数,读者最容易误读的一处
    _presence_sum = sum(tool_presence["per_tool_call_latency_ms"].values())
    freq_note = (
        '<p class="note"><b>「出现日志数」口径</b>:该工具在多少条日志里至少出现过一次'
        f"(一条日志内调用 10 次也只计 1 条),分母为全样本 {n:,} 条日志。"
        "它不是该工具在全样本的调用总次数——两者口径不同,不可与调用数混比"
        f"(本样本调用总数 {total_calls:,} 次,与各工具出现日志数之和 {_presence_sum:,} "
        "不是同一量纲)。耗时/Token 统计值均由<b>有该数值的调用</b>算出,"
        "与出现日志数互相独立。</p>"
    )

    stab_html = (
        '<table class="left"><tr><th>稳定性信号</th><th>命中日志数</th><th>占比</th>'
        "<th>其中未闭环</th>"
        "<th>定义与举例</th></tr>"
    )
    for label, count, desc, dead_hits in stab_signals:
        stab_html += (
            f"<tr><td>{label}</td><td>{count:,}</td><td>{count / n:.1%}</td>"
            f"<td>{dead_hits:,}</td>"
            f'<td class="desc">{desc}</td></tr>'
        )
    stab_html += "</table>"
    stab_html += (
        '<p class="note">均为「日志级」信号:只要该条日志里出现过一次,整条计入一次。'
        "同一条日志可以同时命中多个信号,因此各行不能直接相加,占比之和也不等于 100%。"
        "「其中未闭环」为该信号命中日志里最终未闭环的条数,是原始计数而非派生倍数。</p>"
    )

    def img(b64, alt=""):
        return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;border:1px solid #e0e0e0;margin:8px 0;">'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{args.title}</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 20px; color: #222; }}
h1 {{ border-bottom: 3px solid #1565c0; padding-bottom: 8px; }}
h2 {{ color: #1565c0; margin-top: 36px; }}
h3 {{ color: #333; }}
table {{ border-collapse: collapse; margin: 10px 0; font-size: 13px; }}
th, td {{ border: 1px solid #ccc; padding: 5px 9px; text-align: right; }}
th {{ background: #e3f2fd; }}
td:first-child, th:first-child {{ text-align: left; }}
table.left th, table.left td {{ text-align: left; vertical-align: top; }}
.insight {{ background: #fff8e1; border-left: 4px solid #f57c00; padding: 10px 14px; margin: 10px 0; }}
.reco {{ background: #e8f5e9; border-left: 4px solid #2e7d32; padding: 10px 14px; margin: 10px 0; }}
.note {{ color: #888; font-size: 12px; }}
table.left td.desc {{ color: #222; font-size: 13px; line-height: 1.6; }}
</style></head><body>
<h1>{args.title}</h1>
<p class="note"><b>数据日期: {data_range_disp}</b> · 数据源: {args.csv.name} · 研究模式全量 {n:,} 条日志 · 生成自 .agents/skills/metric-report/scripts/generate_report.py</p>

<h2>0. 样本与可计算性</h2>
{build_sample_summary(rows, args.csv.name)}

<h2>1. 两版指标横向对比(列集增删)</h2>
{build_evolution_section()}

<h2>2. 宽表指标统计总结</h2>
<h3>2.1 标量指标分位数(p50/p80/p90/p95/p99)</h3>
{quant_html}
<h3>2.2 核心指标分箱频次与分位数(总 Token / 总时延)</h3>
{img(charts["percentile_bars"], "分位数柱状图")}

<h3>2.3 稳定性维度信号分布</h3>
{img(charts["stability_dist"], "稳定性信号")}
{stab_html}

<h2>3. 分布与排名</h2>
<h3>3.1 工具耗时排名(平均单次调用)</h3>
{img(charts["tool_latency_rank"], "工具耗时")}
{tool_table_html(tool_tables["per_tool_call_latency_ms"][0], "耗时(ms)")}
{freq_note}
<h3>3.2 工具使用频率排名</h3>
{img(charts["tool_freq_rank"], "工具频率")}

<h3>3.3 工具错误归因</h3>
{img(te_attr_img, "工具错误归因分布与量")}
{te_dist_txt}
{te_insight_txt}
{img(te_matrix_img, "工具失败率与归因矩阵")}
{te_matrix_txt}

<h3>3.4 模型层错误分类与路由</h3>
{img(llm_error_img, "模型错误")}
{llm_error_table}
{llm_error_note}
<h3>3.5 框架 API 重试归因</h3>
{img(api_retry_img, "API重试归因")}
{api_retry_text}

<h2>4. bad case 洞察</h2>
{insight_html}

<h2>5. Agent 迭代建议</h2>
{reco_html}

</body></html>"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_cn_commas(html), encoding="utf-8")
    print(f"已写出: {args.output}")
    print(f"  样本日志数: {n:,}")
    print(f"  分位数表指标数: {len(quant_rows)}")
    print(
        f"  逐工具表: latency {len(tool_tables['per_tool_call_latency_ms'][0])} 工具, "
        f"token {len(tool_tables['per_tool_call_tokens'][0])} 工具"
    )


if __name__ == "__main__":
    main()
