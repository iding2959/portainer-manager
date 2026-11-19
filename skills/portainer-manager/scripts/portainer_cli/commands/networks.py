"""networks：Docker 网络（overlay / bridge）与谁在用它。"""

from typing import Any, Dict, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import as_list, docker_get
from ..output import dump_json, print_table, short_id


def list_networks(cfg: Config, args: Any) -> int:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/networks"
    networks = as_list(docker_get(cfg, "networks"), path)

    if args.scope:
        networks = [item for item in networks if (item.get("Scope") or "") == args.scope]

    rows: List[List[Any]] = []
    for item in networks:
        ipam = item.get("IPAM") or {}
        subnets = ",".join(
            config.get("Subnet") or "" for config in (ipam.get("Config") or [])
        ) or "-"
        rows.append([
            item.get("Name"),
            short_id(item.get("Id")),
            item.get("Driver"),
            item.get("Scope"),
            "yes" if item.get("Attachable") else "no",
            "yes" if item.get("Internal") else "no",
            subnets,
        ])

    if args.json:
        dump_json(networks)
        return EXIT_OK
    print_table(
        ["名称", "网络ID", "驱动", "范围", "可附加", "内部", "子网"],
        rows,
        max_column_width=40,
    )
    print(f"\n共 {len(rows)} 个网络")
    return EXIT_OK


def get_network(cfg: Config, args: Any) -> int:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/networks"
    networks = as_list(docker_get(cfg, "networks"), path)
    matched = None
    for item in networks:
        if item.get("Name") == args.network or short_id(item.get("Id")) == args.network:
            matched = item
            break
    if matched is None:
        names = "、".join(item.get("Name") or "?" for item in networks)
        raise SystemExit(f"错误: 找不到网络 '{args.network}'（可用：{names}）")
    if args.json:
        dump_json(matched)
        return EXIT_OK
    containers: Dict[str, Any] = matched.get("Containers") or {}
    rows = [
        ["名称", matched.get("Name")],
        ["网络ID", matched.get("Id")],
        ["驱动", matched.get("Driver")],
        ["范围", matched.get("Scope")],
        ["可附加", bool(matched.get("Attachable"))],
        ["内部", bool(matched.get("Internal"))],
        ["连接容器数", len(containers)],
    ]
    print_table(["字段", "值"], rows, max_column_width=80)
    if containers:
        detail = [
            [value.get("Name"), short_id(key), value.get("IPv4Address")]
            for key, value in containers.items()
        ]
        print("\n接入的容器")
        print_table(["名称", "容器ID", "IPv4"], detail, max_column_width=50)
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "networks", "Docker 网络", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "网络列表", common)
    lister.add_argument("--scope", choices=("local", "swarm"), help="按范围过滤")
    lister.set_defaults(func=list_networks)

    getter = add_sub(sub, "get", "网络详情（含接入容器）", common)
    getter.add_argument("network", help="网络名或短 id")
    getter.set_defaults(func=get_network)