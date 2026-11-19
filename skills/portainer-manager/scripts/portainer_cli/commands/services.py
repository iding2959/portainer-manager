"""services：Swarm 服务视图（只读 + 经栈回写的扩缩容）。

⚠️ 关键限制：Portainer 的 Docker 代理层**不放行 PUT/POST**（`404 page not found`），
只有 GET 与 DELETE 通。所以「扩缩容/改镜像」不能直接 PUT 服务，必须改栈文件后
重新部署（`stacks update`）。本模块的 scale/restart 都走这条路。

只读部分（list/get/logs）直接用 Docker 代理，信息最真。
"""

from typing import Any, Dict, List, Tuple

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import as_list, docker_get, docker_path, get_json, request, require_ok
from ..output import (
    dump_json,
    images_of,
    print_table,
    service_replicas,
    service_short_name,
    short_id,
    stack_of,
    strip_digest,
)
from ..resolve import all_services, resolve_service


def _replica_state(cfg: Config) -> Dict[str, Dict[str, int]]:
    """按 service id 统计任务数：running 与期望运行数。"""
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/tasks"
    tasks = as_list(docker_get(cfg, "tasks"), path)
    stats: Dict[str, Dict[str, int]] = {}
    for task in tasks:
        service_id = task.get("ServiceID")
        if not service_id:
            continue
        bucket = stats.setdefault(service_id, {"running": 0, "desired": 0})
        state = (task.get("Status") or {}).get("State") or ""
        desired = task.get("DesiredState") or ""
        if state == "running":
            bucket["running"] += 1
        if desired == "running":
            bucket["desired"] += 1
    return stats


def _ports(service: Dict[str, Any]) -> str:
    ports = ((service.get("Endpoint") or {}).get("Ports") or [])
    if not ports:
        return "-"
    return ",".join(
        f"{item.get('PublishedPort')}->{item.get('TargetPort')}" for item in ports
    )


def list_services(cfg: Config, args: Any) -> int:
    services = all_services(cfg)
    if args.stack:
        services = [s for s in services if stack_of(s) == args.stack]
    if args.search:
        needle = args.search.lower()
        services = [
            s for s in services
            if needle in (s.get("Spec") or {}).get("Name", "").lower()
        ]
    stats = _replica_state(cfg)

    rows: List[List[Any]] = []
    for service in sorted(services, key=lambda s: (stack_of(s), (s.get("Spec") or {}).get("Name") or "")):
        mode, replicas = service_replicas(service)
        bucket = stats.get(service.get("ID") or "", {})
        running = bucket.get("running", 0)
        if mode == "global":
            replica_text = f"{running}/global"
        else:
            replica_text = f"{running}/{replicas if replicas is not None else 0}"
        rows.append([
            stack_of(service),
            service_short_name(service),
            mode,
            replica_text,
            _ports(service),
            images_of(service),
            short_id(service.get("ID")),
        ])

    if args.json:
        dump_json(services)
        return EXIT_OK
    print_table(
        ["栈", "服务", "模式", "运行/期望", "端口", "镜像", "服务ID"],
        rows,
        max_column_width=56,
    )
    print(f"\n共 {len(rows)} 个服务")
    return EXIT_OK


def get_service(cfg: Config, args: Any) -> int:
    service = resolve_service(cfg, args.service)
    if args.json:
        dump_json(service)
        return EXIT_OK

    spec = service.get("Spec") or {}
    container = ((spec.get("TaskTemplate") or {}).get("ContainerSpec") or {})
    mode, replicas = service_replicas(service)
    rows: List[List[Any]] = [
        ["服务ID", service.get("ID")],
        ["全名", spec.get("Name")],
        ["栈", stack_of(service)],
        ["模式", mode],
        ["副本", replicas if replicas is not None else "global（按节点数）"],
        ["版本", (service.get("Version") or {}).get("Index")],
        ["镜像", strip_digest(container.get("Image") or "")],
        ["端口", _ports(service)],
        ["约束", (spec.get("TaskTemplate") or {}).get("Placement", {}).get("Constraints") or "-"],
        ["更新时间", service.get("UpdatedAt")],
    ]
    update_status = service.get("UpdateStatus") or {}
    if update_status:
        rows.append([
            "最近更新",
            f"{(update_status.get('State') or '-')} {(update_status.get('Message') or '')}".strip(),
        ])
    print_table(["字段", "值"], rows, max_column_width=90)

    env = container.get("Env") or []
    if env:
        print(f"\n环境变量（{len(env)} 个；含密钥，谨慎外传）")
        print_table(["KEY=VALUE"], [[item] for item in env], max_column_width=100)
    return EXIT_OK


def scale_service(cfg: Config, args: Any) -> int:
    """扩缩容只能改栈文件后重新部署——代理层不放行 PUT /services。"""
    service = resolve_service(cfg, args.service)
    namespace = stack_of(service)
    if namespace == "-":
        raise UsageError(
            f"服务 '{service['Spec']['Name']}' 不属于任何栈（无 com.docker.stack.namespace 标签），"
            "无法通过栈更新改副本数；用 ssh 执行 docker service update 改"
        )
    if args.replicas < 0:
        raise UsageError("--replicas 不能为负")
    stack = _find_stack(cfg, namespace)
    content = (get_json(cfg, f"/api/stacks/{stack['Id']}/file") or {}).get("StackFileContent") or ""
    short_name = service_short_name(service)
    target = args.replicas

    if not args.yes and not args.dry_run:
        raise UsageError(
            f"把服务 '{short_name}'（栈 {namespace}）副本数改为 {target} 会重新部署整个栈，"
            "须显式加 --yes；先加 --dry-run 看将提交的栈文件"
        )

    new_content, changed = _set_replicas(content, short_name, target)
    if not changed and target != 0:
        raise UsageError(
            f"栈文件里没找到服务 '{short_name}' 的 deploy.replicas 行；"
            "若该服务是 global 模式或未声明 replicas，请直接编辑栈文件后用 stacks update --file"
        )

    path = f"/api/stacks/{stack['Id']}?endpointId={stack['EndpointId']}"
    body = {"StackFileContent": new_content, "Env": stack.get("Env") or [], "Prune": False}
    if args.dry_run:
        print(f"将 PUT {path}：{short_name} replicas -> {target}")
        dump_json({"StackFileContent": new_content, "Env": body["Env"], "Prune": False})
        return EXIT_OK

    require_ok(request(cfg, "PUT", path, data=body))
    if args.json:
        dump_json({"stack": stack["Id"], "service": short_name, "replicas": target})
    else:
        print(f"栈 '{namespace}' 已重新部署：{short_name} 目标副本数 {target}")
        print("用 `services list --stack %s` 核对运行数（重新部署需要几十秒）" % namespace)
    return EXIT_OK


def _find_stack(cfg: Config, name: str) -> Dict[str, Any]:
    from ..resolve import all_stacks

    for stack in all_stacks(cfg):
        if stack.get("Name") == name:
            return stack
    raise UsageError(f"服务所属栈 '{name}' 在 Portainer 里没有记录（可能由 docker CLI 直接部署）")


def _set_replicas(content: str, service: str, replicas: int) -> "tuple[str, bool]":
    """在目标服务块内改写 deploy.replicas；块内没有 replicas 就补一行。

    行级处理（标准库无 YAML）。流程分两步：先把内容切成 [(起始行, 结束行)] 的服务块区间，
    定位目标块；再在区间内找 replicas（有则改值，没有则插到 deploy: 下一行）。
    这样不会误伤顶层 networks:/volumes:，也不会重复插入。
    """
    lines = content.splitlines()
    service_indent = _service_block_indent(content)
    services_indent = _services_indent(content)

    # 1) 切出 services 块内的所有服务区间
    blocks: List[Tuple[int, int, str]] = []
    in_services = False
    current_start = -1
    current_name = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("services:"):
            in_services = True
            continue
        if not in_services:
            continue
        if stripped and indent <= services_indent:
            if current_start >= 0:
                blocks.append((current_start, index - 1, current_name))
                current_start = -1
            in_services = False
            continue
        if indent == service_indent and stripped.endswith(":") and not stripped.startswith("#"):
            if current_start >= 0:
                blocks.append((current_start, index - 1, current_name))
            current_start = index
            current_name = stripped[:-1].strip()
    if current_start >= 0:
        blocks.append((current_start, len(lines) - 1, current_name))

    target = None
    for begin, finish, name in blocks:
        if name == service:
            target = (begin, finish)
            break
    if target is None:
        return content, False

    begin, finish = target
    # 2) 区间内找 replicas
    for index in range(begin + 1, finish + 1):
        stripped = lines[index].strip()
        indent = len(lines[index]) - len(lines[index].lstrip())
        if stripped.startswith("replicas:") and indent > service_indent:
            lines[index] = " " * indent + f"replicas: {replicas}"
            return "\n".join(lines) + ("\n" if content.endswith("\n") else ""), True

    # 3) 没有 replicas：插到 deploy: 的下一行（保持同一缩进层级 +2）
    for index in range(begin + 1, finish + 1):
        stripped = lines[index].strip()
        indent = len(lines[index]) - len(lines[index].lstrip())
        if stripped == "deploy:":
            lines.insert(index + 1, " " * (indent + 2) + f"replicas: {replicas}")
            return "\n".join(lines) + ("\n" if content.endswith("\n") else ""), True

    return content, False


def _services_indent(content: str) -> int:
    """顶层 services: 的缩进（通常 0）。"""
    for line in content.splitlines():
        if line.strip().startswith("services:"):
            return len(line) - len(line.lstrip())
    return 0


def _service_block_indent(content: str) -> int:
    """探测 services: 下服务名的缩进量（见 stacks._service_block_indent 的同名逻辑）。"""
    in_services = False
    services_indent = 0
    for line in content.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not in_services:
            if stripped.startswith("services:"):
                in_services = True
                services_indent = indent
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if indent <= services_indent:
            return services_indent + 2
        if stripped.endswith(":"):
            return indent
    return 2


def logs_service(cfg: Config, args: Any) -> int:
    """服务日志：Portainer 代理不提供 logs 端点，改为按服务的任务找容器再取日志。"""
    service = resolve_service(cfg, args.service)
    service_id = service.get("ID") or ""
    containers_path = f"/api/endpoints/{cfg.endpoint_id}/docker/containers/json"
    containers = as_list(
        get_json(cfg, containers_path + "?all=1"), containers_path
    )
    matched = [
        item
        for item in containers
        if (item.get("Labels") or {}).get("com.docker.swarm.service.id") == service_id
    ]
    if not matched:
        raise UsageError(f"服务 '{service['Spec']['Name']}' 当前没有容器（副本可能为 0）")
    matched.sort(key=lambda item: item.get("Created") or 0, reverse=True)
    container_id = matched[0].get("Id")
    logs = require_ok(
        request(
            cfg,
            "GET",
            docker_path(
                cfg,
                f"containers/{container_id}/logs?stdout=1&stderr=1&tail={args.tail}"
                "&timestamps=false",
            ),
        )
    ).text
    print(logs, end="" if logs.endswith("\n") else "\n")
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "services", "Swarm 服务（列表/详情/扩缩容/日志）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "服务列表（含运行/期望副本）", common)
    lister.add_argument("--stack", metavar="S", help="只看某个栈的服务")
    lister.add_argument("--search", metavar="S", help="按服务名模糊过滤")
    lister.set_defaults(func=list_services)

    getter = add_sub(sub, "get", "服务详情（含环境变量）", common)
    getter.add_argument("service", help="服务全名 / 栈内简称 / 栈:服务")
    getter.set_defaults(func=get_service)

    scaler = add_sub(sub, "scale", "改副本数（走栈重新部署，需 --yes）", common)
    scaler.add_argument("service", help="服务全名 / 栈内简称 / 栈:服务")
    scaler.add_argument("-r", "--replicas", type=int, required=True, help="目标副本数（0=停机）")
    scaler.add_argument("--dry-run", action="store_true", help="只打印将提交的栈文件")
    scaler.add_argument("--yes", action="store_true", help="确认执行（危险操作必需）")
    scaler.set_defaults(func=scale_service)

    logger = add_sub(sub, "logs", "查看服务日志（取最新任务容器）", common)
    logger.add_argument("service", help="服务全名 / 栈内简称 / 栈:服务")
    logger.add_argument("--tail", type=int, default=200, help="最近行数，默认 200")
    logger.set_defaults(func=logs_service)