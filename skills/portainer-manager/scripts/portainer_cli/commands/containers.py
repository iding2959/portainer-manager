"""containers：容器列表与详情（含 Swarm 归属）。"""

from typing import Any, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import as_list, docker_get
from ..output import dump_json, print_table, short_id, strip_digest


def _ports(container: dict) -> str:
    ports = container.get("Ports") or []
    published = [
        f"{item.get('PublicPort')}->{item.get('PrivatePort')}"
        for item in ports
        if item.get("PublicPort")
    ]
    return ",".join(published) or "-"


def list_containers(cfg: Config, args: Any) -> int:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/containers/json"
    query = "?all=1" if args.all else ""
    containers = as_list(docker_get(cfg, "containers/json" + query), path + query)

    if args.stack:
        containers = [
            item
            for item in containers
            if (item.get("Labels") or {}).get("com.docker.stack.namespace") == args.stack
        ]
    if args.search:
        needle = args.search.lower()
        containers = [
            item
            for item in containers
            if any(needle in (name or "").lower() for name in (item.get("Names") or []))
        ]

    rows: List[List[Any]] = []
    for item in containers:
        labels = item.get("Labels") or {}
        name = ((item.get("Names") or ["-"])[0] or "-").lstrip("/")
        health = (item.get("Health") or {}).get("Status") or "-"
        rows.append([
            short_id(item.get("Id")),
            name,
            labels.get("com.docker.stack.namespace") or "-",
            (item.get("State") or "-") + (f"/{health}" if health != "-" else ""),
            item.get("Status"),
            _ports(item),
            strip_digest(item.get("Image") or ""),
        ])

    if args.json:
        dump_json(containers)
        return EXIT_OK
    print_table(
        ["容器ID", "名称", "栈", "状态", "运行时长", "端口", "镜像"],
        rows,
        max_column_width=48,
    )
    print(f"\n共 {len(rows)} 个容器")
    return EXIT_OK

def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "containers", "容器（含 Swarm 归属）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "容器列表", common)
    lister.add_argument("--all", action="store_true", help="含已停止容器")
    lister.add_argument("--stack", metavar="S", help="只看某个栈的容器")
    lister.add_argument("--search", metavar="S", help="按容器名模糊过滤")
    lister.set_defaults(func=list_containers)
