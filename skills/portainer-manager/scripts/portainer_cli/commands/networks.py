"""networks：Docker 网络（overlay / bridge）与谁在用它。"""

from typing import Any, Dict, List

from .. import ApiError, EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import as_list, docker_get, docker_path, request, require_ok
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


def create_network(cfg: Config, args: Any) -> int:
    """创建网络。overlay + attachable 是 Swarm 上最常用的组合（普通 docker run 也能接入）。"""
    body: Dict[str, Any] = {"Name": args.name, "Driver": args.driver}
    if args.overlay:
        body["Driver"] = "overlay"
        body["Attachable"] = True    # 不加 attachable 的话非 swarm 容器接不进来
        body["Scope"] = "swarm"
    if args.internal:
        body["Internal"] = True
    if args.subnet or args.gateway:
        ipam_config: Dict[str, Any] = {}
        if args.subnet:
            ipam_config["Subnet"] = args.subnet
        if args.gateway:
            ipam_config["Gateway"] = args.gateway
        body["IPAM"] = {"Driver": "default", "Config": [ipam_config]}
    if args.label:
        labels: Dict[str, str] = {}
        for item in args.label:
            if "=" not in item:
                raise UsageError(f"--label 需要 K=V 形式，收到: {item}")
            key, _, value = item.partition("=")
            labels[key] = value
        body["Labels"] = labels

    path = docker_path(cfg, "networks/create")
    if args.dry_run:
        print(f"将 POST {path}")
        dump_json(body)
        return EXIT_OK

    payload = require_ok(request(cfg, "POST", path, data=body)).json()
    if args.json:
        dump_json(payload)
    else:
        print(f"网络 '{args.name}' 已创建（{body.get('Driver')}，id={short_id(payload.get('Id'))}）")
    return EXIT_OK


def delete_network(cfg: Config, args: Any) -> int:
    """删除网络。

    Docker 自带保护：还有容器接入时会 `409 network ... has active endpoints`，
    所以这里不自己拦（但 `--yes` 仍然是必需的，因为删错了要重建且 IP 会变）。
    预置网络（bridge/host/none/ingress）删不掉，也不该删。
    """
    networks = as_list(docker_get(cfg, "networks"), f"/api/endpoints/{cfg.endpoint_id}/docker/networks")
    matched = None
    for item in networks:
        if item.get("Name") == args.network or short_id(item.get("Id")) == args.network:
            matched = item
            break
    if matched is None:
        names = "、".join(i.get("Name") or "?" for i in networks)
        raise UsageError(f"找不到网络 '{args.network}'（可用：{names}）")

    name = matched.get("Name") or ""
    if name in ("bridge", "host", "none", "ingress", "docker_gwbridge"):
        raise UsageError(f"'{name}' 是 Docker/Swarm 预置网络，不能删除")

    attached = matched.get("Containers") or {}
    if attached and not args.force:
        raise UsageError(
            f"网络 '{name}' 上还接着 {len(attached)} 个容器，拒绝删除。\n"
            f"（{'、'.join(list(attached.values())[0].get('Name', '?') for _ in [0])}…）"
        )
    if not args.yes:
        raise UsageError(f"删除网络 '{name}' 须显式加 --yes")

    try:
        require_ok(request(cfg, "DELETE", docker_path(cfg, f"networks/{matched.get('Id')}")))
    except ApiError as exc:
        if "active endpoints" in str(exc):
            raise UsageError(
                f"网络 '{name}' 仍有端点接入，Docker 拒绝删除：{exc}\n先断开/删除接入的容器或服务。"
            ) from exc
        raise

    if args.json:
        dump_json({"deleted": name})
    else:
        print(f"网络 '{name}' 已删除")
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "networks", "Docker 网络（增删查）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "网络列表", common)
    lister.add_argument("--scope", choices=("local", "swarm"), help="按范围过滤")
    lister.set_defaults(func=list_networks)

    getter = add_sub(sub, "get", "网络详情（含接入容器）", common)
    getter.add_argument("network", help="网络名或短 id")
    getter.set_defaults(func=get_network)

    creator = add_sub(sub, "create", "创建网络", common)
    creator.add_argument("name", help="网络名")
    creator.add_argument("--driver", default="bridge", metavar="D", help="驱动，默认 bridge")
    creator.add_argument("--overlay", action="store_true", help="建 Swarm overlay 网络（自动 attachable）")
    creator.add_argument("--subnet", metavar="CIDR", help="子网，如 10.0.9.0/24")
    creator.add_argument("--gateway", metavar="IP", help="网关")
    creator.add_argument("--internal", action="store_true", help="创建内部网络（无外网出口）")
    creator.add_argument("--label", action="append", metavar="K=V", help="标签，可重复")
    creator.add_argument("--dry-run", action="store_true", help="只打印将提交的请求体")
    creator.set_defaults(func=create_network)

    deleter = add_sub(sub, "delete", "删除网络（需 --yes）", common)
    deleter.add_argument("network", help="网络名或短 id")
    deleter.add_argument("--force", action="store_true", help="即使有容器接入也提交（Docker 仍会 409 拒绝）")
    deleter.add_argument("--yes", action="store_true", help="确认删除")
    deleter.set_defaults(func=delete_network)
