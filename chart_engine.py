"""学术图表引擎：模型只产出 JSON 规格，渲染由本模块确定性完成。

设计原则
- 绝不执行模型生成的 Python 代码（安全），只接受受约束的 JSON 规格。
- 数据图表：matplotlib + seaborn，输出 PNG(300dpi) / SVG。
- 流程图 / 架构图：优先用系统 graphviz `dot`，没有就退回纯 matplotlib 绘制，保证永远有图。
- Excel：优先 xlsxwriter（能塞原生可编辑图表），否则退回 openpyxl（贴图 + 数据表）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

FIGURE_KINDS = ("bar", "line", "scatter", "box", "heatmap", "pie", "flow")

# 排查用：把 True 时让异常直接抛出（命令行里 `ce._DEBUG = True` 即可）
_DEBUG = False

KIND_LABELS = {
    "auto": "自动（让模型判断）",
    "bar": "柱状图 bar",
    "line": "折线图 line",
    "scatter": "散点图 scatter",
    "box": "箱线图 box",
    "heatmap": "热力图 heatmap",
    "pie": "饼图 pie",
    "flow": "流程图 / 架构图 flow",
}

# ---------------------------------------------------------------------------
# matplotlib 中文与学术样式
# ---------------------------------------------------------------------------
_CJK_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "PingFang SC",
    "SimSun",
)


def _cjk_font() -> str:
    try:
        from matplotlib import font_manager

        available = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:  # noqa: BLE001
        return "DejaVu Sans"
    for name in _CJK_CANDIDATES:
        if name in available:
            return name
    return "DejaVu Sans"


def _setup_style() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cjk = _cjk_font()
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [cjk, "DejaVu Sans"],
            "axes.unicode_minus": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.linestyle": "--",
            "grid.linewidth": 0.6,
            "grid.alpha": 0.45,
            "axes.titlesize": 12,
            "axes.labelsize": 10.5,
            "xtick.labelsize": 9.5,
            "ytick.labelsize": 9.5,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "figure.dpi": 120,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
        }
    )


# ---------------------------------------------------------------------------
# 规格解析 / 规范化
# ---------------------------------------------------------------------------
def parse_figure_spec(text: str) -> dict | None:
    """从模型输出里抠出 JSON 规格（容忍 ```json 围栏与前后废话）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    candidates = [fenced.group(1)] if fenced else []
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                parsed = json.loads(attempt)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(parsed, dict):
                return normalize_spec(parsed)
    return None


def _numbers(values) -> list[float]:
    out: list[float] = []
    for value in values or []:
        try:
            out.append(float(value))
        except Exception:  # noqa: BLE001
            out.append(float("nan"))
    return out


def normalize_spec(raw: dict) -> dict:
    """把模型给的 JSON 收拾成渲染器能吃的样子。"""
    spec = dict(raw or {})
    kind = str(spec.get("kind") or spec.get("type") or "bar").strip().lower()
    if kind in {"barh", "bar_chart", "column"}:
        kind = "bar"
    if kind in {"line_chart", "trend", "curve"}:
        kind = "line"
    if kind in {"boxplot", "box_plot", "distribution"}:
        kind = "box"
    if kind in {"heat", "heat_map", "matrix"}:
        kind = "heatmap"
    if kind in {"flowchart", "architecture", "diagram", "pipeline", "flow_chart", "arch"}:
        kind = "flow"
    if kind not in FIGURE_KINDS:
        kind = "bar"

    out = {
        "kind": kind,
        "title": str(spec.get("title", "") or ""),
        "xlabel": str(spec.get("xlabel", "") or ""),
        "ylabel": str(spec.get("ylabel", "") or ""),
        "caption": str(spec.get("caption", "") or ""),
        "source": str(spec.get("source", "") or ""),
    }

    series = []
    for item in spec.get("series") or []:
        if not isinstance(item, dict):
            continue
        series.append(
            {
                "name": str(item.get("name", "") or ""),
                "values": _numbers(item.get("values") or item.get("data") or []),
            }
        )
    out["series"] = [s for s in series if s["values"]]
    out["categories"] = [str(c) for c in (spec.get("categories") or [])]
    out["x"] = _numbers(spec.get("x") or [])
    out["y"] = _numbers(spec.get("y") or [])
    points = []
    for point in spec.get("points") or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append([float(point[0]), float(point[1])])
            except Exception:  # noqa: BLE001
                continue
    out["points"] = points
    matrix = []
    for row in spec.get("matrix") or []:
        if isinstance(row, (list, tuple)):
            matrix.append(_numbers(row))
    out["matrix"] = matrix
    out["matrix_x"] = [str(c) for c in (spec.get("matrix_x") or [])]
    out["matrix_y"] = [str(c) for c in (spec.get("matrix_y") or [])]

    nodes = []
    for node in spec.get("nodes") or []:
        if isinstance(node, dict):
            nodes.append(
                {
                    "id": str(node.get("id") or node.get("label") or ""),
                    "label": str(node.get("label") or node.get("id") or ""),
                    "group": str(node.get("group", "") or ""),
                }
            )
        elif isinstance(node, str):
            nodes.append({"id": node, "label": node, "group": ""})
    out["nodes"] = nodes
    edges = []
    for edge in spec.get("edges") or []:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            edges.append([str(edge[0]), str(edge[1]), str(edge[2]) if len(edge) > 2 else ""])
        elif isinstance(edge, dict):
            edges.append(
                [
                    str(edge.get("from", "")),
                    str(edge.get("to", "")),
                    str(edge.get("label", "") or ""),
                ]
            )
    out["edges"] = edges
    out["data_table"] = spec.get("data_table") if isinstance(spec.get("data_table"), dict) else {}
    return out


def spec_summary(spec: dict) -> str:
    """给用户看的一句话摘要（也用来判断规格是否有内容）。"""
    kind = spec.get("kind", "bar")
    if kind == "flow":
        return f"流程图：{len(spec.get('nodes') or [])} 个节点 / {len(spec.get('edges') or [])} 条连线"
    counts = [len(s.get("values") or []) for s in spec.get("series") or []]
    if counts:
        return f"{KIND_LABELS.get(kind, kind)}：{len(counts)} 组数据，最多 {max(counts)} 个点"
    if spec.get("points"):
        return f"{KIND_LABELS.get(kind, kind)}：{len(spec['points'])} 个散点"
    if spec.get("matrix"):
        return f"{KIND_LABELS.get(kind, kind)}：{len(spec['matrix'])}×{max(len(r) for r in spec['matrix'])} 矩阵"
    return f"{KIND_LABELS.get(kind, kind)}（数据不足，只有标题）"


# ---------------------------------------------------------------------------
# 数据图表
# ---------------------------------------------------------------------------
def _axes_title(ax, spec: dict) -> None:
    if spec.get("title"):
        ax.set_title(spec["title"], pad=10)
    if spec.get("xlabel"):
        ax.set_xlabel(spec["xlabel"])
    if spec.get("ylabel"):
        ax.set_ylabel(spec["ylabel"])


def _draw_data(spec: dict):
    """画数据类图，返回 matplotlib Figure。"""
    _setup_style()
    import matplotlib.pyplot as plt

    kind = spec["kind"]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    series = spec.get("series") or []
    categories = spec.get("categories") or []

    if kind == "bar":
        if not series:
            raise ValueError("柱状图缺少 series 数据")
        labels = categories or [str(i + 1) for i in range(len(series[0]["values"]))]
        n = len(series)
        width = 0.8 / max(n, 1)
        for i, item in enumerate(series):
            values = item["values"][: len(labels)]
            positions = [j + (i - (n - 1) / 2) * width for j in range(len(values))]
            ax.bar(positions, values, width=width * 0.92, label=item.get("name") or f"组 {i + 1}")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=0 if len(labels) <= 6 else 35, ha="right" if len(labels) > 6 else "center")
        ax.grid(axis="x", visible=False)

    elif kind == "line":
        if not series:
            raise ValueError("折线图缺少 series 数据")
        xs = spec.get("x") or list(range(1, len(series[0]["values"]) + 1))
        for i, item in enumerate(series):
            ys = item["values"][: len(xs)]
            ax.plot(xs[: len(ys)], ys, marker="o", markersize=4, linewidth=1.8, label=item.get("name") or f"组 {i + 1}")
        if categories and not spec.get("x"):
            ax.set_xticks(xs)
            ax.set_xticklabels(categories[: len(xs)], rotation=0 if len(categories) <= 6 else 35)
        ax.grid(axis="x", visible=False)
    elif kind == "scatter":
        if not series and not spec.get("points"):
            raise ValueError("散点图缺少 points 或 series 数据")
        for i, item in enumerate(series):
            xs = spec.get("x") or list(range(len(item["values"])))
            ax.scatter(xs[: len(item["values"])], item["values"], s=42, label=item.get("name") or f"组 {i + 1}")
        if spec.get("points"):
            px = [p[0] for p in spec["points"]]
            py = [p[1] for p in spec["points"]]
            ax.scatter(px, py, s=42, label="散点")
    elif kind == "box":
        data = [item["values"] for item in series] or ([spec["y"]] if spec.get("y") else [])
        if not data:
            raise ValueError("箱线图缺少数据")
        names = [item.get("name") or f"组 {i + 1}" for i, item in enumerate(series)] or ["样本"]
        box = ax.boxplot(data, patch_artist=True, medianprops={"linewidth": 1.6})
        for patch in box["boxes"]:
            patch.set_facecolor("#b9efc4")
            patch.set_edgecolor("#1d4a2c")
        ax.set_xticks(range(1, len(names) + 1))
        ax.set_xticklabels(names[: len(data)])
        ax.grid(axis="x", visible=False)
    elif kind == "heatmap":
        matrix = spec.get("matrix") or [item["values"] for item in series]
        if not matrix:
            raise ValueError("热力图缺少 matrix 数据")
        data = matrix
        image = ax.imshow(data, aspect="auto", cmap="Greens")
        fig.colorbar(image, ax=ax, shrink=0.82)
        if spec.get("matrix_x"):
            ax.set_xticks(range(len(spec["matrix_x"])))
            ax.set_xticklabels(spec["matrix_x"], rotation=35, ha="right")
        if spec.get("matrix_y"):
            ax.set_yticks(range(len(spec["matrix_y"])))
            ax.set_yticklabels(spec["matrix_y"])
        for i, row in enumerate(data):
            for j, value in enumerate(row):
                ax.text(j, i, f"{value:g}", ha="center", va="center", fontsize=8.5, color="#10321f")
        ax.grid(False)
    elif kind == "pie":
        values = (series[0]["values"] if series else spec.get("y")) or []
        if not values:
            raise ValueError("饼图缺少数据")
        labels = categories or [f"类别 {i + 1}" for i in range(len(values))]
        ax.pie(values, labels=labels[: len(values)], autopct="%1.1f%%", startangle=90)
        ax.grid(False)
    else:
        raise ValueError(f"不支持的数据图表类型：{kind}")

    if kind != "pie":
        _axes_title(ax, spec)
    else:
        ax.set_title(spec.get("title", ""))
    handles, legend_labels = ax.get_legend_handles_labels()
    if len(legend_labels) > 1 or (legend_labels and spec.get("points")):
        # 图例放到画布右侧，避免压住柱子/折线
        ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False)
    if spec.get("source"):
        fig.text(0.01, -0.02, f"数据来源：{spec['source']}", fontsize=8, color="#4b6b58")
    fig.tight_layout()
    return fig


def _figure_bytes(fig, fmt: str) -> bytes:
    buffer = BytesIO()
    fig.savefig(buffer, format=fmt)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 流程图 / 架构图
# ---------------------------------------------------------------------------
def _flow_dot(spec: dict) -> str:
    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    lines = [
        "digraph G {",
        '  graph [rankdir=TB, splines=ortho, nodesep=0.5, ranksep=0.55, bgcolor="white"];',
        '  node [shape=box, style="rounded,filled", fillcolor="#eafff1", color="#1d4a2c", '
        'fontname="Microsoft YaHei", fontsize=11];',
        '  edge [color="#1d4a2c", arrowsize=0.7, fontname="Microsoft YaHei", fontsize=9];',
    ]
    for i, node in enumerate(nodes):
        label = (node.get("label") or node.get("id") or f"N{i}").replace('"', "'")
        lines.append(f'  "{node.get("id") or i}" [label="{label}"];')
    for edge in edges:
        src, dst, label = edge[0], edge[1], edge[2] if len(edge) > 2 else ""
        label = str(label).replace('"', "'")
        if label:
            lines.append(f'  "{src}" -> "{dst}" [label="{label}"];')
        else:
            lines.append(f'  "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines)


def _render_flow_matplotlib(spec: dict, fmt: str) -> bytes:
    """没有 graphviz 时的兜底：按拓扑层级竖向排布方框 + 箭头。"""
    _setup_style()
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    if not nodes:
        raise ValueError("流程图缺少 nodes")

    ids = [n.get("id") or f"n{i}" for i, n in enumerate(nodes)]
    labels = {n.get("id") or f"n{i}": n.get("label") or n.get("id") or "" for i, n in enumerate(nodes)}

    # 简单分层：入度为 0 的节点在第 0 层，逐层下推
    depth = {node_id: 0 for node_id in ids}
    outs = {node_id: [] for node_id in ids}
    for edge in edges:
        src, dst = edge[0], edge[1]
        if src in outs and dst in depth:
            outs[src].append(dst)
    for _ in range(len(ids)):
        changed = False
        for src, targets in outs.items():
            for dst in targets:
                if depth[dst] < depth[src] + 1:
                    depth[dst] = depth[src] + 1
                    changed = True
        if not changed:
            break

    levels: dict[int, list[str]] = {}
    for node_id in ids:
        levels.setdefault(depth[node_id], []).append(node_id)
    n_levels = max(levels) + 1
    height = 1.1 * n_levels + 0.6
    fig, ax = plt.subplots(figsize=(7.2, max(3.2, height)))
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    box_w, box_h = 0.30, 0.10
    positions: dict[str, tuple[float, float]] = {}
    for level, members in levels.items():
        y = 1 - (level + 0.7) / n_levels
        for index, node_id in enumerate(members):
            x = (index + 1) / (len(members) + 1)
            positions[node_id] = (x, y)
            ax.add_patch(
                FancyBboxPatch(
                    (x - box_w / 2, y - box_h / 2),
                    box_w,
                    box_h,
                    boxstyle="round,pad=0.012,rounding_size=0.02",
                    linewidth=1.4,
                    edgecolor="#1d4a2c",
                    facecolor="#eafff1",
                    zorder=2,
                )
            )
            ax.text(x, y, labels.get(node_id, node_id), ha="center", va="center", fontsize=9.5, color="#10321f", zorder=3)

    for edge in edges:
        src, dst = edge[0], edge[1]
        if src not in positions or dst not in positions:
            continue
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1 - box_h / 2),
                (x2, y2 + box_h / 2),
                arrowstyle="-|>",
                mutation_scale=12,
                linewidth=1.3,
                color="#1d4a2c",
                shrinkA=0,
                shrinkB=0,
                zorder=1,
            )
        )
        if len(edge) > 2 and edge[2]:
            ax.text((x1 + x2) / 2 + 0.01, (y1 + y2) / 2, str(edge[2]), fontsize=8.5, color="#3c6b4e")

    if spec.get("title"):
        ax.set_title(spec["title"], fontsize=12)
    fig.tight_layout()
    return _figure_bytes(fig, fmt)


def flow_dot_source(spec: dict) -> bytes:
    """返回 graphviz DOT 源码，方便用户在本机 Graphviz / 编辑器里继续改。"""
    return _flow_dot(spec).encode("utf-8")


def render_flow(spec: dict, fmt: str = "png") -> tuple[bytes, str]:
    """返回 (图片字节, 渲染后端名)。有 graphviz 就用它，否则 matplotlib 兜底。"""
    dot = shutil.which("dot")
    if dot:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "graph.dot"
                source.write_text(_flow_dot(spec), encoding="utf-8")
                target = Path(tmp) / f"graph.{fmt}"
                subprocess.run(
                    [dot, f"-T{fmt}", str(source), "-o", str(target)],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                if target.exists() and target.stat().st_size > 0:
                    return target.read_bytes(), "graphviz"
        except Exception:  # noqa: BLE001 - 退回 matplotlib
            pass
    return _render_flow_matplotlib(spec, fmt), "matplotlib"


def render_figure(spec: dict, fmt: str = "png") -> tuple[bytes, str]:
    """渲染任意图表，返回 (字节, 后端说明)。"""
    fmt = "svg" if str(fmt).lower() == "svg" else "png"
    if spec.get("kind") == "flow":
        return render_flow(spec, fmt)
    fig = _draw_data(spec)
    return _figure_bytes(fig, fmt), "matplotlib"


# ---------------------------------------------------------------------------
# Excel 导出
# ---------------------------------------------------------------------------
def _spec_table(spec: dict) -> tuple[list[str], list[list]]:
    """把规格摊平成 (表头, 行) 供 Excel 数据页使用。"""
    if spec.get("kind") == "flow":
        headers = ["节点 id", "节点名称", "分组"]
        rows = [[n.get("id", ""), n.get("label", ""), n.get("group", "")] for n in spec.get("nodes") or []]
        if spec.get("edges"):
            rows.append([])
            rows.append(["连线", "起点", "终点", "标签"])
            for edge in spec["edges"]:
                rows.append(["→", edge[0], edge[1], edge[2] if len(edge) > 2 else ""])
        return headers, rows

    series = spec.get("series") or []
    if series:
        categories = spec.get("categories") or []
        length = max(len(item["values"]) for item in series)
        headers = ["序号", "类别"] + [item.get("name") or f"组 {i + 1}" for i, item in enumerate(series)]
        rows = []
        for i in range(length):
            category = categories[i] if i < len(categories) else ""
            row = [i + 1, category]
            for item in series:
                values = item["values"]
                row.append(values[i] if i < len(values) else "")
            rows.append(row)
        return headers, rows
    if spec.get("points"):
        return ["x", "y"], [[p[0], p[1]] for p in spec["points"]]
    if spec.get("matrix"):
        headers = ["行\\列"] + [str(c) for c in (spec.get("matrix_x") or range(len(spec["matrix"][0])))]
        rows = []
        for i, row in enumerate(spec["matrix"]):
            label = spec["matrix_y"][i] if i < len(spec.get("matrix_y") or []) else f"行 {i + 1}"
            rows.append([label] + list(row))
        return headers, rows
    table = spec.get("data_table") or {}
    if table.get("headers"):
        return [str(h) for h in table["headers"]], [list(r) for r in table.get("rows") or []]
    return ["提示"], [["模型没有返回可摊平的数据"]]


def figure_to_xlsx(spec: dict, image_png: bytes | None = None) -> bytes:
    """导出 .xlsx：数据页 + 原生图表（可编辑）+ 渲染好的图片页。"""
    headers, rows = _spec_table(spec)
    title = spec.get("title") or "图表"

    try:
        import xlsxwriter

        buffer = BytesIO()
        book = xlsxwriter.Workbook(buffer, {"in_memory": True})
        bold = book.add_format({"bold": True, "bg_color": "#eafff1", "border": 1})
        sheet = book.add_worksheet("数据")
        for col, header in enumerate(headers):
            sheet.write(0, col, header, bold)
        for r, row in enumerate(rows, start=1):
            if not row:
                continue
            for c, value in enumerate(row):
                if isinstance(value, (int, float)):
                    sheet.write_number(r, c, value)
                else:
                    sheet.write_string(r, c, str(value))
        if headers:
            sheet.set_column(0, max(len(headers) - 1, 0), 16)

        kind = spec.get("kind", "bar")
        if kind in {"bar", "line", "scatter"} and spec.get("series") and len(rows) >= 2:
            chart = book.add_chart(
                {
                    "type": {"bar": "column", "line": "line", "scatter": "scatter"}.get(kind, "column")
                }
            )
            # 数据页列序：A 序号 / B 类别 / C.. 各组数值（0 基列号 = index + 2）
            for index, item in enumerate(spec["series"]):
                name = item.get("name") or f"组 {index + 1}"
                value_col = index + 2
                category_col = 1
                if kind == "scatter":
                    series_spec = {
                        "name": name,
                        "categories": ["数据", 1, 0, len(rows), 0],
                        "values": ["数据", 1, value_col, len(rows), value_col],
                    }
                else:
                    series_spec = {
                        "name": name,
                        "categories": ["数据", 1, category_col, len(rows), category_col],
                        "values": ["数据", 1, value_col, len(rows), value_col],
                    }
                chart.add_series(series_spec)
            chart.set_title({"name": title})
            if spec.get("xlabel"):
                chart.set_x_axis({"name": spec["xlabel"]})
            if spec.get("ylabel"):
                chart.set_y_axis({"name": spec["ylabel"]})
            chart.set_size({"width": 640, "height": 400})
            sheet.insert_chart("H2", chart)

        if image_png:
            image_sheet = book.add_worksheet("图")
            image_sheet.insert_image("B2", "figure.png", {"image_data": BytesIO(image_png)})
            image_sheet.set_column(0, 0, 3)

        note = book.add_worksheet("说明")
        note.write(0, 0, "标题", bold)
        note.write(0, 1, title)
        note.write(1, 0, "类型", bold)
        note.write(1, 1, KIND_LABELS.get(kind, kind))
        note.write(2, 0, "X 轴", bold)
        note.write(2, 1, spec.get("xlabel", ""))
        note.write(3, 0, "Y 轴", bold)
        note.write(3, 1, spec.get("ylabel", ""))
        note.write(4, 0, "数据来源", bold)
        note.write(4, 1, spec.get("source", ""))
        note.set_column(0, 0, 12)
        note.set_column(1, 1, 60)
        book.close()
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - 退回 openpyxl
        if _DEBUG:
            raise

    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, PatternFill

    book = Workbook()
    sheet = book.active
    sheet.title = "数据"
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="EAFFF1")
    for row in rows:
        if row:
            sheet.append(row)
    for col in sheet.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        sheet.column_dimensions[col[0].column_letter].width = min(max(12, width + 4), 48)
    if image_png:
        image_sheet = book.create_sheet("图")
        image_sheet.add_image(XLImage(BytesIO(image_png)), "B2")
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
