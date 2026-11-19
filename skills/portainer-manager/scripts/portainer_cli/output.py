"""渲染层：表格（中文双宽近似）、JSON 输出、单位换算。

所有的人类可读输出都从这里出，保证表格列宽/单位口径在所有子命令里一致。
"""

import json
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence, Tuple


WIDE_WIDTH_CHARS = ("W", "F")


def dump_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in WIDE_WIDTH_CHARS else 1 for ch in text)


def clip_text(text: str, limit: int) -> str:
    if display_width(text) <= limit:
        return text
    kept: List[str] = []
    width = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in WIDE_WIDTH_CHARS else 1
        if width + char_width > limit - 1:
            break
        kept.append(char)
        width += char_width
    return "".join(kept) + "…"


def format_value(value: Any) -> str:
    if value is None:
        return "-"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value) or "-"
    return str(value)


def format_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    max_column_width: int = 52,
) -> str:
    header_cells = [clip_text(str(header), max_column_width) for header in headers]
    body_cells = [
        [clip_text(format_value(cell), max_column_width) for cell in row] for row in rows
    ]
    widths = [display_width(cell) for cell in header_cells]
    for row in body_cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(cell))

    def render(cells: Sequence[str]) -> str:
        padded = [
            cell + " " * (widths[index] - display_width(cell))
            for index, cell in enumerate(cells)
        ]
        return "  ".join(padded).rstrip()

    lines = [render(header_cells), "  ".join("-" * width for width in widths)]
    lines.extend(render(row) for row in body_cells)
    return "\n".join(lines)


def print_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    max_column_width: int = 52,
    empty_hint: str = "（无数据）",
) -> None:
    if not rows:
        print(empty_hint)
        return
    print(format_table(headers, rows, max_column_width=max_column_width))


def human_bytes(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if abs(number) < 1024 or unit == units[-1]:
            return f"{int(number)}B" if unit == "B" else f"{number:.1f}{unit}"
        number /= 1024
    return f"{number:.1f}PiB"


def nano_cpus(value: Any) -> str:
    """Docker 用 NanoCPUs 表示核数（1 核 = 1e9）。"""
    if value is None:
        return "-"
    try:
        return f"{float(value) / 1e9:g}"
    except (TypeError, ValueError):
        return str(value)


def short_id(value: Any, length: int = 12) -> str:
    text = str(value or "")
    return text[:length] if text else "-"


def strip_digest(image: str) -> str:
    """镜像常带 @sha256:… 摘要，表格里显示会挤爆列宽。"""
    return (image or "").split("@", 1)[0] or "-"


def service_replicas(service: Dict[str, Any]) -> Tuple[str, Any]:
    """返回 (模式标签, 期望副本数)；global 模式没有 Replicas 字段。"""
    mode = (service.get("Spec") or {}).get("Mode") or {}
    if "Global" in mode:
        return "global", None
    replicated = mode.get("Replicated") or {}
    return "replicated", replicated.get("Replicas")


def stack_of(service: Dict[str, Any]) -> str:
    """服务名形如 <栈>_<服务>，栈名在 label 里更可靠（无栈服务没有该 label）。"""
    labels = (service.get("Spec") or {}).get("Labels") or {}
    return labels.get("com.docker.stack.namespace") or "-"


def service_short_name(service: Dict[str, Any]) -> str:
    """把 n8n_n8n 这种「栈名前缀重复」的显示成 n8n_n8n（原名），栈内简称另给一列。"""
    name = (service.get("Spec") or {}).get("Name") or ""
    namespace = stack_of(service)
    if namespace and namespace != "-" and name.startswith(namespace + "_"):
        return name[len(namespace) + 1 :]
    return name


def images_of(service: Dict[str, Any]) -> str:
    container = ((service.get("Spec") or {}).get("TaskTemplate") or {}).get("ContainerSpec") or {}
    return strip_digest(container.get("Image") or "")