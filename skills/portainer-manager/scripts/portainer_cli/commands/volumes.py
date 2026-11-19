"""volumes：Docker 卷（清理与占用排查）。"""

from typing import Any, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import docker_get
from ..output import dump_json, human_bytes, print_table


def list_volumes(cfg: Config, args: Any) -> int:
    payload = docker_get(cfg, "volumes")
    volumes = (payload or {}).get("Volumes") or []

    if args.dangling:
        volumes = [item for item in volumes if not (item.get("UsageData") or {}).get("RefCount")]
    if args.search:
        needle = args.search.lower()
        volumes = [item for item in volumes if needle in (item.get("Name") or "").lower()]

    rows: List[List[Any]] = []
    for item in volumes:
        usage = item.get("UsageData") or {}
        labels = item.get("Labels") or {}
        rows.append([
            item.get("Name"),
            item.get("Driver"),
            item.get("Scope"),
            human_bytes(usage.get("Size")),
            usage.get("RefCount") if usage else "-",
            labels.get("com.docker.stack.namespace") or "-",
        ])

    if args.json:
        dump_json(volumes)
        return EXIT_OK
    print_table(
        ["名称", "驱动", "范围", "大小", "引用", "栈"],
        rows,
        max_column_width=60,
    )
    print(f"\n共 {len(rows)} 个卷" + ("（仅未被引用的悬空卷）" if args.dangling else ""))
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "volumes", "Docker 卷", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "卷列表", common)
    lister.add_argument("--dangling", action="store_true", help="只看未被任何容器引用的卷")
    lister.add_argument("--search", metavar="S", help="按卷名模糊过滤")
    lister.set_defaults(func=list_volumes)