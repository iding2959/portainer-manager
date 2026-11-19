"""images：镜像列表（按体积/悬空镜像排查磁盘）。"""

import datetime
from typing import Any, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import as_list, docker_get
from ..output import dump_json, human_bytes, print_table, short_id


def _tags(item: dict) -> str:
    tags = item.get("RepoTags") or []
    return ",".join(tags) if tags else "<none>"


def _created(epoch: Any) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def list_images(cfg: Config, args: Any) -> int:
    images = as_list(docker_get(cfg, "images/json"), f"/api/endpoints/{cfg.endpoint_id}/docker/images/json")

    if args.search:
        needle = args.search.lower()
        images = [item for item in images if needle in _tags(item).lower()]
    if args.dangling:
        images = [item for item in images if not (item.get("RepoTags") or [])]

    images.sort(key=lambda item: item.get("Size") or 0, reverse=True)
    if args.limit:
        images = images[: args.limit]

    rows: List[List[Any]] = []
    for item in images:
        rows.append([
            short_id(item.get("Id"), 19),
            _tags(item),
            human_bytes(item.get("Size")),
            _created(item.get("Created")),
            "yes" if (item.get("Containers") or 0) > 0 else "-",
        ])

    if args.json:
        dump_json(images)
        return EXIT_OK
    print_table(
        ["镜像ID", "标签", "大小", "创建时间", "在用"],
        rows,
        max_column_width=64,
    )
    print(f"\n共 {len(rows)} 个镜像（按大小降序）")
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "images", "镜像（磁盘占用排查）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "镜像列表", common)
    lister.add_argument("--search", metavar="S", help="按镜像标签模糊过滤")
    lister.add_argument("--dangling", action="store_true", help="只看无标签悬空镜像")
    lister.add_argument("--limit", type=int, help="只显示最大的 N 个")
    lister.set_defaults(func=list_images)