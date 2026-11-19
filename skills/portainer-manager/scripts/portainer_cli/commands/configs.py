"""configs / secrets：Swarm 配置对象。

Portainer UI 的 Configs / Secrets 两个菜单对应 Docker Swarm 的 `/configs` 与 `/secrets`，
走的是同一套 Docker 透传通道（`/api/endpoints/{id}/docker/...`）。

**只读是刻意的**：secret 的 create 需要把明文塞进请求体，而 CLI 是公开脚本，
一旦封装就有被写成「把口令当参数传」的风险；真需要建，走 `docker secret create` 或 `raw`。

安全约束：secret 的值**永远不打印**（Docker 本身也不返回，只回 ID/名称/时间）。
"""

from typing import Any, Dict, List

from .. import ApiError, EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import as_list, docker_get, docker_path, request, require_ok
from ..output import dump_json, print_table, short_id


def _list_objects(cfg: Config, kind: str) -> List[Dict[str, Any]]:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/{kind}"
    return as_list(docker_get(cfg, kind), path)


def _rows(objects: List[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for item in objects:
        spec = item.get("Spec") or {}
        labels = spec.get("Labels") or {}
        # 栈创建的 config/secret 会带命名空间标签，便于按栈归组
        namespace = labels.get("com.docker.stack.namespace") or "-"
        rows.append([
            short_id(item.get("ID")),
            spec.get("Name") or "-",
            namespace,
            (item.get("CreatedAt") or "")[:19],
            len(labels),
        ])
    return rows


def _show(cfg: Config, args: Any, kind: str, title: str) -> int:
    objects = _list_objects(cfg, kind)
    if args.search:
        needle = args.search.lower()
        objects = [o for o in objects if needle in ((o.get("Spec") or {}).get("Name") or "").lower()]

    if args.json:
        dump_json(objects)
        return EXIT_OK

    rows = _rows(objects)
    print_table(["ID", "名称", "栈", "创建时间", "标签数"], rows, max_column_width=44)
    print(f"\n共 {len(rows)} 个 {title}")
    if kind == "secrets":
        # 这句要留着：否则使用者会以为 CLI 漏打了值
        print("（secret 的值无法读取，Docker 本身不返回；要轮换就重新 create 再更新服务引用）")
    return EXIT_OK


def _inspect(cfg: Config, args: Any, kind: str) -> int:
    objects = _list_objects(cfg, kind)
    wanted = args.name
    matched = None
    for item in objects:
        spec = item.get("Spec") or {}
        if spec.get("Name") == wanted or short_id(item.get("ID")) == wanted:
            matched = item
            break
    if matched is None:
        names = "、".join((o.get("Spec") or {}).get("Name") or "?" for o in objects) or "（空）"
        from .. import UsageError

        raise UsageError(f"找不到 {kind} '{wanted}'（可用：{names}）")

    if args.json:
        dump_json(matched)
        return EXIT_OK

    spec = matched.get("Spec") or {}
    labels = spec.get("Labels") or {}
    rows = [
        ["ID", matched.get("ID")],
        ["名称", spec.get("Name")],
        ["栈", labels.get("com.docker.stack.namespace") or "-"],
        ["创建时间", (matched.get("CreatedAt") or "")[:19]],
        ["版本", (matched.get("Version") or {}).get("Index")],
        ["标签", labels or "-"],
    ]
    print_table(["字段", "值"], rows, max_column_width=70)

    # 引用它的服务：这才是运维真正关心的——改了要不要重启谁
    users = _services_using(cfg, spec.get("Name") or "", kind)
    if users:
        print(f"\n引用它的服务（{len(users)} 个）")
        print_table(["服务全名"], [[name] for name in users], max_column_width=60)
    return EXIT_OK


def _services_using(cfg: Config, name: str, kind: str) -> List[str]:
    """扫全部服务，找出挂载了该 config/secret 的那些。

    Docker 不提供反向索引，只能客户端遍历 services 的 Spec 比对名字。
    """
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/services"
    try:
        services = as_list(docker_get(cfg, "services"), path)
    except Exception:  # 权限不足时不要让整个命令失败，引用关系只是附加信息
        return []
    field = "Secrets" if kind == "secrets" else "Configs"
    found: List[str] = []
    for service in services:
        container = (((service.get("Spec") or {}).get("TaskTemplate") or {}).get("ContainerSpec") or {})
        for ref in container.get(field) or []:
            if ((ref.get("File") or {}).get("Name") == name) or ((ref.get("ConfigName") or ref.get("SecretName")) == name):
                found.append((service.get("Spec") or {}).get("Name") or "-")
                break
    return sorted(set(found))


def list_configs(cfg: Config, args: Any) -> int:
    return _show(cfg, args, "configs", "config")


def get_config(cfg: Config, args: Any) -> int:
    return _inspect(cfg, args, "configs")


def list_secrets(cfg: Config, args: Any) -> int:
    return _show(cfg, args, "secrets", "secret")


def get_secret(cfg: Config, args: Any) -> int:
    return _inspect(cfg, args, "secrets")


def create_object(cfg: Config, args: Any, kind: str) -> int:
    """创建 config / secret。

    Docker 要求 base64：config 的 `Data` 与 secret 的 `Data` 都是 base64 编码的字节。
    这里接受两种输入：`--from-file` 读文件，或 `--from-stdin` 读标准输入；
    不接受明文命令行参数——命令行会留在 shell history 和进程列表里。
    """
    import base64
    import sys

    if args.from_file:
        try:
            with open(args.from_file, "rb") as handle:
                payload = handle.read()
        except OSError as exc:
            raise UsageError(f"--from-file 无法读取: {args.from_file}: {exc}")
    elif args.from_stdin:
        payload = sys.stdin.buffer.read()
    else:
        raise UsageError(
            "需要 --from-file PATH 或 --from-stdin 提供内容。\n"
            "刻意不支持命令行明文传值：会留在 shell history 与进程列表里。"
        )
    if not payload:
        raise UsageError("内容为空，拒绝创建")

    body: Dict[str, Any] = {
        "Name": args.name,
        "Data": base64.b64encode(payload).decode("ascii"),
    }
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

    path = docker_path(cfg, f"{kind}/create")
    if args.dry_run:
        preview = dict(body)
        preview["Data"] = f"<base64 {len(payload)} 字节，已省略>"
        print(f"将 POST {path}")
        dump_json(preview)
        return EXIT_OK

    payload_resp = require_ok(request(cfg, "POST", path, data=body)).json()
    if args.json:
        dump_json(payload_resp)
    else:
        print(f"{kind[:-1]} '{args.name}' 已创建（id={short_id(payload_resp.get('ID'))}，{len(payload)} 字节）")
    return EXIT_OK


def delete_object(cfg: Config, args: Any, kind: str) -> int:
    """删除 config / secret。

    Docker 对仍被服务引用的 secret/config 会拒绝（409），所以这里先做一次引用反查，
    给出「哪些服务在用」再让人决定——比直接吃 409 错误更好排查。
    """
    objects = _list_objects(cfg, kind)
    matched = None
    for item in objects:
        if (item.get("Spec") or {}).get("Name") == args.name or short_id(item.get("ID")) == args.name:
            matched = item
            break
    if matched is None:
        names = "、".join((o.get("Spec") or {}).get("Name") or "?" for o in objects) or "（空）"
        raise UsageError(f"找不到 {kind[:-1]} '{args.name}'（可用：{names}）")

    name = (matched.get("Spec") or {}).get("Name") or ""
    users = _services_using(cfg, name, kind)
    if users and not args.force:
        raise UsageError(
            f"{kind[:-1]} '{name}' 正被服务引用（{'、'.join(users)}），拒绝删除。\n"
            "先更新这些服务去掉引用，或加 --force（Docker 仍会拒绝在用对象）。"
        )
    if not args.yes:
        raise UsageError(f"删除 {kind[:-1]} '{name}' 须显式加 --yes（不可恢复）")

    try:
        require_ok(request(cfg, "DELETE", docker_path(cfg, f"{kind}/{matched.get('ID')}")))
    except ApiError as exc:
        if "in use" in str(exc).lower() or "409" in str(exc):
            raise UsageError(f"{kind[:-1]} '{name}' 仍被引用，Docker 拒绝删除：{exc}") from exc
        raise

    if args.json:
        dump_json({"deleted": name})
    else:
        print(f"{kind[:-1]} '{name}' 已删除")
    return EXIT_OK


def create_config(cfg: Config, args: Any) -> int:
    return create_object(cfg, args, "configs")


def delete_config(cfg: Config, args: Any) -> int:
    return delete_object(cfg, args, "configs")


def create_secret(cfg: Config, args: Any) -> int:
    return create_object(cfg, args, "secrets")


def delete_secret(cfg: Config, args: Any) -> int:
    return delete_object(cfg, args, "secrets")


def _add_write_args(parser: Any) -> None:
    parser.add_argument("--from-file", metavar="PATH", help="从文件读取内容（推荐）")
    parser.add_argument("--from-stdin", action="store_true", help="从标准输入读取内容")
    parser.add_argument("--label", action="append", metavar="K=V", help="标签，可重复")
    parser.add_argument("--stack", metavar="S", help="打上栈归属标签")
    parser.add_argument("--dry-run", action="store_true", help="只打印将提交的请求体（内容已省略）")


def register(subparsers: Any, common: Any) -> None:
    configs = add_sub(subparsers, "configs", "Swarm 配置对象（增删查）", common)
    sub = configs.add_subparsers(dest="action", metavar="<动作>")
    lister = add_sub(sub, "list", "config 列表", common)
    lister.add_argument("--search", metavar="S", help="按名称模糊过滤")
    lister.set_defaults(func=list_configs)
    getter = add_sub(sub, "get", "config 详情 + 引用它的服务", common)
    getter.add_argument("name", help="config 名称或短 ID")
    getter.set_defaults(func=get_config)
    creator = add_sub(sub, "create", "创建 config（从文件或 stdin）", common)
    creator.add_argument("name", help="config 名称")
    _add_write_args(creator)
    creator.set_defaults(func=create_config)
    deleter = add_sub(sub, "delete", "删除 config（需 --yes）", common)
    deleter.add_argument("name", help="config 名称或短 ID")
    deleter.add_argument("--force", action="store_true", help="即使被服务引用也提交")
    deleter.add_argument("--yes", action="store_true", help="确认删除")
    deleter.set_defaults(func=delete_config)

    secrets = add_sub(subparsers, "secrets", "Swarm 密钥对象（增删查，值不可读）", common)
    sub2 = secrets.add_subparsers(dest="action", metavar="<动作>")
    slister = add_sub(sub2, "list", "secret 列表", common)
    slister.add_argument("--search", metavar="S", help="按名称模糊过滤")
    slister.set_defaults(func=list_secrets)
    sgetter = add_sub(sub2, "get", "secret 详情 + 引用它的服务", common)
    sgetter.add_argument("name", help="secret 名称或短 ID")
    sgetter.set_defaults(func=get_secret)
    screator = add_sub(sub2, "create", "创建 secret（从文件或 stdin）", common)
    screator.add_argument("name", help="secret 名称")
    _add_write_args(screator)
    screator.set_defaults(func=create_secret)
    sdeleter = add_sub(sub2, "delete", "删除 secret（需 --yes）", common)
    sdeleter.add_argument("name", help="secret 名称或短 ID")
    sdeleter.add_argument("--force", action="store_true", help="即使被服务引用也提交")
    sdeleter.add_argument("--yes", action="store_true", help="确认删除")
    sdeleter.set_defaults(func=delete_secret)
