"""volumes：Docker 卷（查询、详情、删除）。

删除卷是**不可逆**操作，所以 CLI 做了三道闸：
1. 必须 `--yes`；
2. 默认只允许删「未被引用」的卷（`--all` 才允许删有引用的，且仍会被 Docker 拒绝）；
3. 删前先查一次引用情况并打印，让执行者看见影响面。

Docker 自己有保护：删除仍被容器或服务引用的卷会返回
`409 volume is in use - [<容器id>]`，所以误删在服务层面不会发生，
但**已停止容器曾写入的数据**卷掉就没有了。
"""

from typing import Any, Dict, List

from .. import ApiError, EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import docker_get, docker_path, request, require_ok
from ..output import dump_json, human_bytes, print_table, short_id


def _all_volumes(cfg: Config) -> List[Dict[str, Any]]:
    payload = docker_get(cfg, "volumes") or {}
    return list(payload.get("Volumes") or [])


def _ref_count(volume: Dict[str, Any]) -> Any:
    """没有 UsageData 时 Docker 不回引用数（本环境常态），返回 None 表示未知。"""
    usage = volume.get("UsageData") or {}
    return usage.get("RefCount")


def list_volumes(cfg: Config, args: Any) -> int:
    volumes = _all_volumes(cfg)

    if args.dangling:
        volumes = [v for v in volumes if not _ref_count(v)]
    if args.search:
        needle = args.search.lower()
        volumes = [v for v in volumes if needle in (v.get("Name") or "").lower()]
    if args.stack:
        volumes = [
            v
            for v in volumes
            if (v.get("Labels") or {}).get("com.docker.stack.namespace") == args.stack
        ]

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
    print_table(["名称", "驱动", "范围", "大小", "引用", "栈"], rows, max_column_width=60)
    print(f"\n共 {len(rows)} 个卷" + ("（仅未被引用的悬空卷）" if args.dangling else ""))
    if rows and all((r[3] == "-" and r[4] == "-") for r in rows):
        # 这句很重要：否则看表格的人会以为卷是空的
        print("提示：大小/引用显示 `-` 是因为 Docker 没回 UsageData（Linux 上常见），不代表卷为空")
    return EXIT_OK


def _find_volume(cfg: Config, name: str) -> Dict[str, Any]:
    for item in _all_volumes(cfg):
        if item.get("Name") == name:
            return item
    names = "、".join((v.get("Name") or "?") for v in _all_volumes(cfg)) or "（空）"
    raise UsageError(f"找不到卷 '{name}'（可用：{names}）")


def get_volume(cfg: Config, args: Any) -> int:
    volume = _find_volume(cfg, args.volume)
    if args.json:
        dump_json(volume)
        return EXIT_OK

    usage = volume.get("UsageData") or {}
    labels = volume.get("Labels") or {}
    rows = [
        ["名称", volume.get("Name")],
        ["驱动", volume.get("Driver")],
        ["挂载点", volume.get("Mountpoint")],
        ["范围", volume.get("Scope")],
        ["创建时间", (volume.get("CreatedAt") or "")[:19]],
        ["大小", human_bytes(usage.get("Size")) if usage else "-（Docker 未提供）"],
        ["引用数", usage.get("RefCount") if usage else "-（未知）"],
        ["栈", labels.get("com.docker.stack.namespace") or "-"],
        ["选项", volume.get("Options") or "-"],
    ]
    print_table(["字段", "值"], rows, max_column_width=80)

    # 引用只是观感，但决定能不能删；用容器列表做一次真实反查
    users = _containers_using(cfg, volume.get("Name") or "")
    if users:
        print(f"\n正在使用它的容器（{len(users)} 个，这些卷删不掉）")
        print_table(["容器", "状态", "挂载点"], users, max_column_width=50)
    return EXIT_OK


def _containers_using(cfg: Config, volume_name: str) -> List[List[Any]]:
    """扫全部容器（含已停止）的 Mounts，找出用了这个卷的。

    Docker 不提供卷→容器的反向索引，只能客户端遍历。
    """
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/containers/json?all=1"
    try:
        containers = docker_get(cfg, "containers/json?all=1") or []
    except ApiError:
        return []
    found: List[List[Any]] = []
    for item in containers:
        for mount in item.get("Mounts") or []:
            if mount.get("Type") == "volume" and mount.get("Name") == volume_name:
                name = ((item.get("Names") or ["-"])[0] or "-").lstrip("/")
                found.append([name, item.get("State"), mount.get("Destination")])
                break
    return found


def delete_volume(cfg: Config, args: Any) -> int:
    volume = _find_volume(cfg, args.volume)
    name = volume.get("Name") or ""

    users = _containers_using(cfg, name)
    if users and not args.force:
        listing = "、".join(str(u[0]) for u in users)
        raise UsageError(
            f"卷 '{name}' 正被容器使用（{listing}），拒绝删除。\n"
            "确认要删的话：先停掉/删除这些容器，或加 --force（Docker 仍会拒绝正在运行的引用）。"
        )

    if not args.yes:
        scale = f"被 {len(users)} 个容器使用" if users else "当前无容器引用"
        raise UsageError(
            f"删除卷 '{name}'（{scale}）**数据不可恢复**，须显式加 --yes。\n"
            "先 `volumes get` 看清挂载点与引用再决定。"
        )

    path = docker_path(cfg, f"volumes/{name}")
    try:
        require_ok(request(cfg, "DELETE", path))
    except ApiError as exc:
        if "volume is in use" in str(exc):
            # 把 Docker 原话转成可操作提示
            raise UsageError(
                f"卷 '{name}' 仍被引用，Docker 拒绝删除：{exc}\n"
                "先删掉引用它的服务/容器，再回来删。"
            ) from exc
        raise

    if args.json:
        dump_json({"deleted": name})
    else:
        print(f"卷 '{name}' 已删除")
    return EXIT_OK


def create_volume(cfg: Config, args: Any) -> int:
    """创建卷。Docker 只支持 name/driver/labels/options，没有更多花活。

    `--stack` 会打上 `com.docker.stack.namespace` 标签，这样它和栈里声明的卷
    在形式上一致（但栈不会自动接管外部创建的卷，这只是便于归类）。
    """
    body: Dict[str, Any] = {"Name": args.name, "Driver": args.driver or "local"}
    labels: Dict[str, str] = {}
    for item in args.label or []:
        if "=" not in item:
            raise UsageError(f"--label 需要 K=V 形式，收到: {item}")
        key, _, value = item.partition("=")
        labels[key] = value
    if args.stack:
        labels["com.docker.stack.namespace"] = args.stack
    if labels:
        body["Labels"] = labels
    if args.opt:
        options: Dict[str, str] = {}
        for item in args.opt:
            if "=" not in item:
                raise UsageError(f"--opt 需要 K=V 形式，收到: {item}")
            key, _, value = item.partition("=")
            options[key] = value
        body["DriverOpts"] = options

    path = docker_path(cfg, "volumes/create")
    if args.dry_run:
        print(f"将 POST {path}")
        dump_json(body)
        return EXIT_OK

    payload = require_ok(request(cfg, "POST", path, data=body)).json()
    if args.json:
        dump_json(payload)
    else:
        print(f"卷 '{payload.get('Name') or args.name}' 已创建（驱动 {payload.get('Driver')}）")
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "volumes", "Docker 卷（增删改查）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "卷列表", common)
    lister.add_argument("--dangling", action="store_true", help="只看未被任何容器引用的卷")
    lister.add_argument("--search", metavar="S", help="按卷名模糊过滤")
    lister.add_argument("--stack", metavar="S", help="只看某个栈的卷")
    lister.set_defaults(func=list_volumes)

    getter = add_sub(sub, "get", "卷详情 + 正在使用它的容器", common)
    getter.add_argument("volume", help="卷名")
    getter.set_defaults(func=get_volume)

    creator = add_sub(sub, "create", "创建卷", common)
    creator.add_argument("name", help="卷名")
    creator.add_argument("--driver", metavar="D", help="驱动，默认 local")
    creator.add_argument("--label", action="append", metavar="K=V", help="标签，可重复")
    creator.add_argument("--opt", action="append", metavar="K=V", help="驱动选项，可重复")
    creator.add_argument("--stack", metavar="S", help="打上栈归属标签（便于归类）")
    creator.add_argument("--dry-run", action="store_true", help="只打印将提交的请求体")
    creator.set_defaults(func=create_volume)

    deleter = add_sub(sub, "delete", "删除卷（需 --yes，数据不可恢复）", common)
    deleter.add_argument("volume", help="卷名")
    deleter.add_argument("--yes", action="store_true", help="确认删除（不可逆）")
    deleter.add_argument(
        "--force", action="store_true",
        help="即使有容器引用也提交删除（Docker 对运行中引用仍会 409 拒绝）",
    )
    deleter.set_defaults(func=delete_volume)
