"""health：Portainer 自身 + Docker 引擎 + Swarm 拓扑三合一探针。

排查顺序是固定的：Portainer 活着 → Docker 引擎可达 → Swarm 状态正常。
任何一步失败，后面的都没意义，所以这里按顺序短路报错。
"""

from typing import Any, Dict, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import docker_get, get_json, request, require_ok
from ..output import dump_json, print_table, short_id


def _portainer_status(cfg: Config) -> Dict[str, Any]:
    """`/api/system/status` 免鉴权，用它判断「连的是不是 Portainer」。"""
    return get_json(cfg, "/api/system/status")


def _endpoint_status(cfg: Config) -> Dict[str, Any]:
    return get_json(cfg, f"/api/endpoints/{cfg.endpoint_id}")


def run(cfg: Config, args: Any) -> int:
    status = _portainer_status(cfg)
    endpoint = _endpoint_status(cfg)
    docker_info = docker_get(cfg, "info")
    docker_version = docker_get(cfg, "version")
    swarm = docker_get(cfg, "swarm")

    snapshot = ((endpoint.get("Snapshots") or [{}])[0]) or {}
    swarm_state = (docker_info.get("Swarm") or {}).get("LocalNodeState") or "-"
    manager_count = (docker_info.get("Swarm") or {}).get("Managers")
    node_count = (docker_info.get("Swarm") or {}).get("Nodes")

    rows: List[List[Any]] = [
        ["Portainer", status.get("Version"), "Portainer CE 自身版本"],
        ["InstanceID", short_id(status.get("InstanceID"), 8), "实例标识（多实例时区分）"],
        ["Endpoint", f"{endpoint.get('Name')} (id={endpoint.get('Id')})", "当前操作的环境"],
        ["EndpointStatus", endpoint.get("Status"), "1=up 2=down"],
        ["Docker", docker_info.get("ServerVersion"), docker_info.get("OperatingSystem") or "-"],
        # info 里的 ApiVersion 实测为 null，真实值只有在 /docker/version 里
        ["ApiVersion", docker_version.get("ApiVersion"), f"min={docker_version.get('MinAPIVersion')}"],
        ["Swarm", swarm_state, f"manager={manager_count} node={node_count}"],
        ["SwarmID", short_id(swarm.get("ID"), 8), f"名称 {(swarm.get('Spec') or {}).get('Name') or '-'}"],
        ["Containers", docker_info.get("Containers"), f"运行中 {docker_info.get('ContainersRunning')}"],
        ["Images", docker_info.get("Images"), "-"],
        ["Stacks", f"{snapshot.get('StackCount')} 栈 / {snapshot.get('ServiceCount')} 服务", "取自 Endpoint 快照，可能滞后"],
        ["Node", f"{docker_info.get('Name')}", f"{docker_info.get('NCPU')} 核 / {docker_info.get('MemTotal')} 字节"],
    ]

    if args.json:
        dump_json(
            {
                "portainer": status,
                "endpoint": endpoint,
                "docker_info": docker_info,
                "docker_version": docker_version,
                "swarm": swarm,
            }
        )
        return EXIT_OK

    print_table(["项", "值", "说明"], rows, max_column_width=60)
    return EXIT_OK

    if args.json:
        dump_json(
            {
                "portainer": status,
                "endpoint": endpoint,
                "docker_info": docker_info,
                "swarm": swarm,
            }
        )
        return EXIT_OK

    print_table(["项", "值", "说明"], rows, max_column_width=60)
    return EXIT_OK


def _watch(cfg: Config, args: Any) -> int:
    """轻量存活检查：只打 /api/system/status，不碰 Docker。"""
    response = require_ok(request(cfg, "GET", "/api/system/status"))
    if args.json:
        dump_json(response.json())
    else:
        data = response.json() or {}
        print(f"Portainer {data.get('Version')} 就绪（HTTP {response.status}）")
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "health", "探针三合：Portainer / Docker 引擎 / Swarm", common)
    parser.set_defaults(func=run)

    watch = add_sub(subparsers, "ping", "只查 Portainer 存活（不碰 Docker）", common)
    watch.set_defaults(func=_watch)