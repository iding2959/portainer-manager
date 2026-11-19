"""nodes：Swarm 节点与资源。"""

from typing import Any, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import as_list, docker_get
from ..output import dump_json, human_bytes, nano_cpus, print_table, short_id


def _role(node: dict) -> str:
    status = node.get("Status") or {}
    manager_status = (node.get("ManagerStatus") or {}).get("Reachability") or ""
    base = (node.get("Spec") or {}).get("Role") or "-"
    if manager_status == "leader":
        return "manager(leader)"
    return base


def list_nodes(cfg: Config, args: Any) -> int:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/nodes"
    nodes = as_list(docker_get(cfg, "nodes"), path)

    rows: List[List[Any]] = []
    for node in nodes:
        description = node.get("Description") or {}
        resources = description.get("Resources") or {}
        spec = node.get("Spec") or {}
        status = node.get("Status") or {}
        engine = description.get("Engine") or {}
        rows.append([
            short_id(node.get("ID")),
            description.get("Hostname"),
            _role(node),
            status.get("State"),
            spec.get("Availability"),
            status.get("Addr"),
            nano_cpus(resources.get("NanoCPUs")),
            human_bytes(resources.get("MemoryBytes")),
            engine.get("EngineVersion"),
            node.get("CreatedAt", "")[:19],
        ])

    if args.json:
        dump_json(nodes)
        return EXIT_OK
    print_table(
        ["节点ID", "主机名", "角色", "状态", "可用性", "地址", "CPU", "内存", "引擎", "加入时间"],
        rows,
        max_column_width=40,
    )
    print(f"\n共 {len(rows)} 个节点")
    return EXIT_OK


def get_node(cfg: Config, args: Any) -> int:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/nodes"
    nodes = as_list(docker_get(cfg, "nodes"), path)
    wanted = args.node
    matched = None
    for node in nodes:
        if short_id(node.get("ID")) == wanted or (node.get("Description") or {}).get("Hostname") == wanted:
            matched = node
            break
    if matched is None:
        hosts = "、".join((n.get("Description") or {}).get("Hostname") or "?" for n in nodes)
        raise SystemExit(f"错误: 找不到节点 '{wanted}'（可用：{hosts}）")
    if args.json:
        dump_json(matched)
        return EXIT_OK
    description = matched.get("Description") or {}
    labels = (matched.get("Spec") or {}).get("Labels") or {}
    rows = [
        ["节点ID", matched.get("ID")],
        ["主机名", description.get("Hostname")],
        ["角色", _role(matched)],
        ["状态", (matched.get("Status") or {}).get("State")],
        ["可用性", (matched.get("Spec") or {}).get("Availability")],
        ["地址", (matched.get("Status") or {}).get("Addr")],
        ["操作系统", (description.get("Platform") or {}).get("OS")],
        ["架构", (description.get("Platform") or {}).get("Architecture")],
        ["引擎", (description.get("Engine") or {}).get("EngineVersion")],
        ["标签", labels or "-"],
        ["资源标签", (matched.get("Spec") or {}).get("Labels") or "-"],
    ]
    print_table(["字段", "值"], rows, max_column_width=80)
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "nodes", "Swarm 节点", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "节点列表", common)
    lister.set_defaults(func=list_nodes)

    getter = add_sub(sub, "get", "节点详情", common)
    getter.add_argument("node", help="节点短 id 或主机名")
    getter.set_defaults(func=get_node)