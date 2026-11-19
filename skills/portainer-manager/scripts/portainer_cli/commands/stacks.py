"""stacks：Portainer 栈（Compose + Swarm stack）管理。

这是**唯一能在 Portainer 侧改服务定义**的通路：Docker 代理层不放行 PUT/POST
（见 references/pitfalls.md 第 1 条），所以扩缩容/改环境变量都要走「改栈文件 + 重新部署」。

危险操作一律 `--yes`，且 `--dry-run` 可先看请求体。
"""

import json
from typing import Any, List

from .. import ApiError, EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import get_json, request, require_ok
from ..output import dump_json, print_table
from ..resolve import all_stacks, resolve_stack


TYPE_SWARM = 1
TYPE_COMPOSE = 2

STATUS_LABEL = {1: "active", 2: "inactive"}


def _stack_row(stack: dict) -> List[Any]:
    stack_type = "swarm" if stack.get("Type") == TYPE_SWARM else "compose"
    return [
        stack.get("Id"),
        stack.get("Name"),
        stack_type,
        f"E{stack.get('EndpointId')}",
        STATUS_LABEL.get(stack.get("Status"), stack.get("Status")),
        (stack.get("GitConfig") or {}).get("URL") or "-",
        stack.get("UpdatedBy") or stack.get("CreatedBy") or "-",
    ]


def list_stacks(cfg: Config, args: Any) -> int:
    stacks = all_stacks(cfg)
    if args.search:
        needle = args.search.lower()
        stacks = [s for s in stacks if needle in (s.get("Name") or "").lower()]
    if args.status:
        wanted = 1 if args.status == "active" else 2
        stacks = [s for s in stacks if s.get("Status") == wanted]
    if args.json:
        dump_json(stacks)
        return EXIT_OK
    rows = [_stack_row(stack) for stack in sorted(stacks, key=lambda s: s.get("Id") or 0)]
    print_table(["ID", "名称", "类型", "环境", "状态", "Git", "更新人"], rows)
    print(f"\n共 {len(rows)} 个栈")
    return EXIT_OK


def get_stack(cfg: Config, args: Any) -> int:
    stack = resolve_stack(cfg, args.stack)
    if args.json:
        dump_json(stack)
        return EXIT_OK
    rows = [[key, stack.get(key)] for key in (
        "Id", "Name", "Type", "EndpointId", "SwarmId", "EntryPoint", "Status",
        "ProjectPath", "CreationDate", "UpdateDate", "CreatedBy", "UpdatedBy",
        "FromAppTemplate", "Namespace",
    )]
    git = stack.get("GitConfig")
    rows.append(["GitConfig", json.dumps(git, ensure_ascii=False) if git else "-"])
    print_table(["字段", "值"], rows, max_column_width=80)
    return EXIT_OK


def file_stack(cfg: Config, args: Any) -> int:
    """打印栈的 compose 文件；--save 落盘便于本地编辑。"""
    stack = resolve_stack(cfg, args.stack)
    payload = get_json(cfg, f"/api/stacks/{stack['Id']}/file")
    content = (payload or {}).get("StackFileContent") or ""
    if args.save:
        with open(args.save, "w", encoding="utf-8") as handle:
            handle.write(content)
        print(f"已写入 {args.save}（{len(content)} 字节）")
        return EXIT_OK
    if args.json:
        dump_json(payload)
        return EXIT_OK
    print(content)
    return EXIT_OK


def _read_file(path: str, what: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise UsageError(f"{what}无法读取: {path}: {exc}")


def update_stack(cfg: Config, args: Any) -> int:
    """改栈内容并重新部署。

    Portainer 的 PUT 要求**回填整个 StackFileContent**（不是 patch），所以这里先取
    远端文件、应用本地替换，再整体提交——避免把没改的部分弄丢。
    """
    stack = resolve_stack(cfg, args.stack)
    remote = get_json(cfg, f"/api/stacks/{stack['Id']}/file") or {}
    content = remote.get("StackFileContent") or ""

    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            content = handle.read()
        source = f"本地文件 {args.file}"
    elif args.set_image:
        parts = args.set_image.split("=", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise UsageError("--set-image 需要 SERVICE=IMAGE 形式，如 n8n=ghcr.io/x/y:tag")
        service, image = parts
        content, changed = _replace_image(content, service.strip(), image.strip())
        if not changed:
            raise UsageError(f"栈文件里没找到服务 '{service}' 的 image 行")
        source = f"--set-image {service}={image}"
    else:
        raise UsageError("update 需要 --file 或 --set-image 指定新内容")

    if not args.yes and not args.dry_run:
        raise UsageError(
            f"更新栈 '{stack['Name']}' 会重新部署其全部服务（{stack['Id']}），"
            "须显式加 --yes；先加 --dry-run 看将要提交的内容"
        )

    # 2.36 起用 RepullImageAndRedeploy 替代已废弃的 PullImage（见官方 spec stacks.updateSwarmStackPayload）
    body = {
        "StackFileContent": content,
        "Env": stack.get("Env") or [],
        "Prune": bool(args.prune),
        "RepullImageAndRedeploy": bool(args.repull),
    }
    path = f"/api/stacks/{stack['Id']}?endpointId={stack['EndpointId']}"
    if args.dry_run:
        preview = dict(body)
        preview["StackFileContent"] = _tail(content, 40)
        print(f"将 PUT {path}（内容来自 {source}）")
        if args.json:
            dump_json(preview)
        else:
            dump_json(preview)
        return EXIT_OK

    response = require_ok(request(cfg, "PUT", path, data=body))
    payload = response.json()

    # PUT 返回 200 不等于改动生效：实测 2.45.1 上曾出现「响应 200、栈文件却没变」，
    # 而后续 scale 会以栈文件为准重新部署，于是把镜像我改回去了。
    # 这里回读栈文件比对，不一致就明确失败，避免静默漂移。
    _verify_stack_file(cfg, stack["Id"], content, source)

    if args.json:
        dump_json(payload)
    else:
        print(f"栈 '{stack['Name']}' 已更新（{source}）")
    return EXIT_OK


def _verify_stack_file(cfg: Config, stack_id: Any, expected: str, source: str) -> None:
    """回读栈文件确认改动真的落盘（PUT 200 不代表生效）。"""
    actual = (get_json(cfg, f"/api/stacks/{stack_id}/file") or {}).get("StackFileContent") or ""
    if _normalize(actual) == _normalize(expected):
        return
    raise ApiError(
        f"栈文件写入未生效：PUT 返回成功，但回读内容与提交的不一致（来源 {source}）。\n"
        "这会导致后续 scale/update 以旧栈文件覆盖本次改动——请重试，或用 "
        "`stacks file` 核对后手工修正。\n"
        f"回读内容特征：{_stack_image_lines(actual)}"
    )


def _normalize(text: str) -> str:
    """忽略行尾空白与末尾换行造成的假差异。"""
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _stack_image_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("image:")]
    return "、".join(lines) or "（无 image 行）"


def _service_block_indent(content: str) -> int:
    """探测 services: 下服务名的缩进量。

    实测 compose 文件缩进不统一（2 空格和 4 空格都见过），硬编码会漏匹配，
    所以从文件本身读：services: 之后第一条「更深的、以冒号结尾的键」就是服务名。
    """
    lines = content.splitlines()
    services_indent = None
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if services_indent is None:
            if stripped == "services:" or stripped.startswith("services:"):
                services_indent = indent
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if indent <= services_indent:
            # services 块结束
            return services_indent + 2
        if stripped.endswith(":"):
            return indent
    return services_indent + 2 if services_indent is not None else 2


def _replace_image(content: str, service: str, image: str) -> "tuple[str, bool]":
    """只替换目标服务块下的 image 行；保持其余内容与缩进不变。

    不用 YAML 库（标准库没有），所以按「服务块」边界做行级替换：缩进等于服务级缩进
    的 `名:` 才切换当前块，只在目标块内改 image。
    """
    service_indent = _service_block_indent(content)
    lines = content.splitlines()
    in_target = False
    changed = False
    result: List[str] = []
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent == service_indent and stripped.endswith(":") and not stripped.startswith("#"):
            in_target = stripped[:-1].strip() == service
        elif in_target and stripped.startswith("image:") and indent > service_indent:
            prefix = line[:indent]
            result.append(f"{prefix}image: {image}")
            changed = True
            continue
        result.append(line)
    return "\n".join(result) + ("\n" if content.endswith("\n") else ""), changed


def _tail(text: str, limit: int) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    return "\n".join(lines[:limit]) + f"\n...（共 {len(lines)} 行，仅预览前 {limit} 行）"


def delete_stack(cfg: Config, args: Any) -> int:
    stack = resolve_stack(cfg, args.stack)
    if not args.yes:
        raise UsageError(
            f"删除栈 '{stack['Name']}'（id={stack['Id']}）会连带删除其全部服务与卷，"
            "须显式加 --yes（危险操作）"
        )
    path = f"/api/stacks/{stack['Id']}?endpointId={stack['EndpointId']}"
    response = require_ok(request(cfg, "DELETE", path))
    if args.json:
        dump_json({"deleted": stack["Id"], "status": response.status})
    else:
        print(f"栈 '{stack['Name']}'（id={stack['Id']}）已删除")
    return EXIT_OK


def _toggle_stack(cfg: Config, args: Any, action: str) -> int:
    """整栈启停：官方 spec 的 StackStart / StackStop，无请求体，endpointId 必填。"""
    stack = resolve_stack(cfg, args.stack)
    if not args.yes:
        raise UsageError(
            f"{'启动' if action == 'start' else '停止'}栈 '{stack['Name']}' 会"
            f"{'拉起' if action == 'start' else '停掉'}其全部服务，须显式加 --yes（影响线上）"
        )
    path = f"/api/stacks/{stack['Id']}/{action}?endpointId={stack['EndpointId']}"
    response = require_ok(request(cfg, "POST", path))
    if args.json:
        dump_json({"stack": stack["Id"], "action": action, "status": response.status})
    else:
        print(f"栈 '{stack['Name']}' 已{('启动' if action == 'start' else '停止')}")
    return EXIT_OK


def start_stack(cfg: Config, args: Any) -> int:
    return _toggle_stack(cfg, args, "start")


def stop_stack(cfg: Config, args: Any) -> int:
    return _toggle_stack(cfg, args, "stop")


def create_stack(cfg: Config, args: Any) -> int:
    """从本地 compose 文件创建 Swarm 栈（POST /stacks/create/swarm/string）。

    `SwarmID` 在官方 spec 里是**必填**（2.39.4 与 2.45.1 都是），不传会得到
    `400 Invalid request payload / Invalid Swarm ID`。它无法从 endpoint 元数据可靠取得
    （快照里的 DockerSnapshotRaw.Swarm 是 null），所以直接查 `/docker/swarm`。
    """
    content = _read_file(args.file, "栈文件")
    swarm_id = args.swarm_id or _current_swarm_id(cfg)
    body = {
        "Name": args.name,
        "StackFileContent": content,
        "SwarmID": swarm_id,
        "Env": [{"name": item.split("=", 1)[0], "value": item.split("=", 1)[1]}
                for item in (args.env or []) if "=" in item],
        "FromAppTemplate": False,
    }
    path = f"/api/stacks/create/swarm/string?endpointId={cfg.endpoint_id}"
    if args.dry_run:
        print(f"将 POST {path}（SwarmID={swarm_id}）")
        dump_json({**body, "StackFileContent": _tail(content, 20)})
        return EXIT_OK
    if not args.yes:
        raise UsageError(
            f"创建栈 '{args.name}' 会在环境 {cfg.endpoint_id} 上真的部署服务，须显式加 --yes"
        )
    payload = require_ok(request(cfg, "POST", path, data=body)).json()
    if args.json:
        dump_json(payload)
    else:
        print(f"栈 '{args.name}' 已创建（id={payload.get('Id')}）")
    return EXIT_OK


def _current_swarm_id(cfg: Config) -> str:
    """当前 endpoint 所属 Swarm 的 ID。

    Endpoint 快照里的 DockerSnapshotRaw.Swarm 实测为 null，只能实时打 /docker/swarm。
    """
    from ..http import docker_get

    swarm = docker_get(cfg, "swarm") or {}
    swarm_id = swarm.get("ID")
    if not swarm_id:
        raise UsageError(
            "取不到当前 Swarm ID：该 endpoint 可能不是 Swarm 环境。"
            "用 --swarm-id 显式指定，或先确认 `health` 里的 Swarm 状态"
        )
    return swarm_id


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "stacks", "Portainer 栈管理（唯一能改服务定义的通路）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "栈列表", common)
    lister.add_argument("--search", metavar="S", help="按名称模糊过滤")
    lister.add_argument("--status", choices=("active", "inactive"), help="按状态过滤")
    lister.set_defaults(func=list_stacks)

    getter = add_sub(sub, "get", "栈详情", common)
    getter.add_argument("stack", help="栈 id 或名称")
    getter.set_defaults(func=get_stack)

    filer = add_sub(sub, "file", "打印栈的 compose 文件", common)
    filer.add_argument("stack", help="栈 id 或名称")
    filer.add_argument("--save", metavar="PATH", help="写入本地文件而不是打印")
    filer.set_defaults(func=file_stack)

    updater = add_sub(sub, "update", "改栈文件并重新部署（需 --yes）", common)
    updater.add_argument("stack", help="栈 id 或名称")
    updater.add_argument("--file", metavar="PATH", help="用本地文件整体替换栈内容")
    updater.add_argument("--set-image", metavar="SERVICE=IMAGE", help="只改某服务的镜像")
    updater.add_argument("--prune", action="store_true", help="删除 compose 里不再定义的服务")
    updater.add_argument(
        "--repull", action="store_true",
        help="强制重新拉取镜像再部署（RepullImageAndRedeploy，替代已废弃的 PullImage）",
    )
    updater.add_argument("--dry-run", action="store_true", help="只打印将提交的内容")
    updater.add_argument("--yes", action="store_true", help="确认执行（危险操作必需）")
    updater.set_defaults(func=update_stack)

    creator = add_sub(sub, "create", "从本地 compose 创建 Swarm 栈（需 --yes）", common)
    creator.add_argument("--name", required=True, help="栈名")
    creator.add_argument("--file", required=True, metavar="PATH", help="compose 文件")
    creator.add_argument("--env", action="append", metavar="K=V", help="栈环境变量，可重复")
    creator.add_argument(
        "--swarm-id", metavar="ID",
        help="Swarm 集群 id（官方 spec 必填）；不传自动从 /docker/swarm 取当前集群",
    )
    creator.add_argument("--dry-run", action="store_true", help="只打印将提交的请求体")
    creator.add_argument("--yes", action="store_true", help="确认创建（危险操作必需）")
    creator.set_defaults(func=create_stack)

    starter = add_sub(sub, "start", "启动整栈（StackStart，需 --yes）", common)
    starter.add_argument("stack", help="栈 id 或名称")
    starter.add_argument("--yes", action="store_true", help="确认执行")
    starter.set_defaults(func=start_stack)

    stopper = add_sub(sub, "stop", "停止整栈（StackStop，需 --yes）", common)
    stopper.add_argument("stack", help="栈 id 或名称")
    stopper.add_argument("--yes", action="store_true", help="确认执行")
    stopper.set_defaults(func=stop_stack)

    deleter = add_sub(sub, "delete", "删除栈（需 --yes）", common)
    deleter.add_argument("stack", help="栈 id 或名称")
    deleter.add_argument("--yes", action="store_true", help="确认删除（危险操作必需）")
    deleter.set_defaults(func=delete_stack)