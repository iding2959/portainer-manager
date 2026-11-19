"""resources：Portainer 自身管理面（endpoints / settings / system status）。"""

from typing import Any, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import as_list, get_json
from ..output import dump_json, human_bytes, print_table, short_id


ENDPOINT_TYPE = {1: "docker", 2: "docker-swarm", 3: "azure", 4: "edge-agent", 5: "k8s"}
STATUS = {1: "up", 2: "down"}


def list_endpoints(cfg: Config, args: Any) -> int:
    endpoints = as_list(get_json(cfg, "/api/endpoints"), "/api/endpoints")
    rows: List[List[Any]] = []
    for item in endpoints:
        snapshot = ((item.get("Snapshots") or [{}])[0]) or {}
        rows.append([
            item.get("Id"),
            item.get("Name"),
            ENDPOINT_TYPE.get(item.get("Type"), item.get("Type")),
            STATUS.get(item.get("Status"), item.get("Status")),
            item.get("URL"),
            snapshot.get("DockerVersion") or "-",
            snapshot.get("StackCount"),
            snapshot.get("ContainerCount"),
            snapshot.get("NodeCount"),
        ])
    if args.json:
        dump_json(endpoints)
        return EXIT_OK
    print_table(
        ["ID", "名称", "类型", "状态", "URL", "Docker", "栈", "容器", "节点"],
        rows,
        max_column_width=44,
    )
    print(f"\n共 {len(rows)} 个环境（--endpoint-id 决定 docker 透传打到哪个）")
    return EXIT_OK


def show_settings(cfg: Config, args: Any) -> int:
    payload = get_json(cfg, "/api/settings/public") or {}
    if args.json:
        dump_json(payload)
        return EXIT_OK
    rows = [[key, payload.get(key)] for key in sorted(payload)]
    print_table(["设置项", "值"], rows, max_column_width=70)
    return EXIT_OK


def system_status(cfg: Config, args: Any) -> int:
    payload = get_json(cfg, "/api/system/status") or {}
    if args.json:
        dump_json(payload)
        return EXIT_OK
    print_table(
        ["项", "值"],
        [["Version", payload.get("Version")], ["InstanceID", short_id(payload.get("InstanceID"), 8)]],
    )
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "endpoints", "Portainer 环境（Endpoint）列表", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")
    lister = add_sub(sub, "list", "环境列表", common)
    lister.set_defaults(func=list_endpoints)

    settings = add_sub(subparsers, "settings", "Portainer 公开设置", common)
    sub2 = settings.add_subparsers(dest="action", metavar="<动作>")
    shower = add_sub(sub2, "show", "显示公开设置", common)
    shower.set_defaults(func=show_settings)