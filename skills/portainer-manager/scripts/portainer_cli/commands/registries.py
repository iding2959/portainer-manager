"""registries：镜像仓库凭据（只读；凭据内容不打印）。"""

from typing import Any, Dict, List

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import get_json, request, require_ok
from ..output import dump_json, print_table


REGISTRY_TYPE = {1: "dockerhub", 2: "ghcr", 3: "custom", 4: "quay", 5: "azure", 6: "gitlab", 7: "ecr", 8: "proget"}


def list_registries(cfg: Config, args: Any) -> int:
    registries = get_json(cfg, "/api/registries") or []
    if args.json:
        # --json 也脱敏：Password 字段绝不回显
        sanitized = []
        for item in registries:
            copy = dict(item)
            for field in ("Password", "Username"):
                if copy.get(field):
                    copy[field] = "***"
            sanitized.append(copy)
        dump_json(sanitized)
        return EXIT_OK
    rows: List[List[Any]] = [
        [
            item.get("Id"),
            item.get("Name"),
            REGISTRY_TYPE.get(item.get("Type"), item.get("Type")),
            item.get("URL"),
            "yes" if item.get("Authentication") else "no",
        ]
        for item in registries
    ]
    print_table(["ID", "名称", "类型", "URL", "需认证"], rows, max_column_width=50)
    print(f"\n共 {len(rows)} 个仓库（凭据内容不显示）")
    return EXIT_OK


def create_registry(cfg: Config, args: Any) -> int:
    """新增镜像仓库凭据。

    口令只从文件或环境变量读，不接受命令行明文——注册表凭据一旦落到 shell history
    就等于泄漏。Portainer 的 Type 枚举见 schemas.md（3=custom，最常用）。
    """
    import os

    if args.password_file:
        try:
            with open(args.password_file, "r", encoding="utf-8") as handle:
                password = handle.read().strip()
        except OSError as exc:
            raise UsageError(f"--password-file 无法读取: {exc}")
    else:
        password = os.environ.get(args.password_env or "PORTAINER_REGISTRY_PASSWORD", "").strip()
    if not password:
        raise UsageError(
            "缺少口令：用 --password-file 或环境变量（默认 $PORTAINER_REGISTRY_PASSWORD）。\n"
            "刻意不支持命令行明文口令。"
        )
    if not args.registry_username and args.type not in (3,):
        raise UsageError("非 custom 类型通常需要 --username")

    body: Dict[str, Any] = {
        "Name": args.name,
        "URL": args.url,
        "Type": args.type,
        "Authentication": True,
        "Username": args.registry_username or "",
        "Password": password,
    }
    if args.registry_base_url:
        body["BaseURL"] = args.registry_base_url
    if args.management:
        body["ManagementConfiguration"] = {"Type": 1}

    if args.dry_run:
        preview = dict(body)
        preview["Password"] = "***"
        print("将 POST /api/registries")
        dump_json(preview)
        return EXIT_OK

    payload = require_ok(request(cfg, "POST", "/api/registries", data=body)).json()
    if args.json:
        sanitized = dict(payload) if isinstance(payload, dict) else payload
        if isinstance(sanitized, dict):
            sanitized["Password"] = "***"
        dump_json(sanitized)
    else:
        print(f"仓库 '{args.name}' 已创建（id={payload.get('Id')}）")
    return EXIT_OK


def delete_registry(cfg: Config, args: Any) -> int:
    registries = get_json(cfg, "/api/registries") or []
    matched = None
    for item in registries:
        if str(item.get("Id")) == str(args.registry) or item.get("Name") == args.registry:
            matched = item
            break
    if matched is None:
        names = "、".join(f"{i.get('Id')}:{i.get('Name')}" for i in registries) or "（空）"
        raise UsageError(f"找不到仓库 '{args.registry}'（可用：{names}）")
    if not args.yes:
        raise UsageError(
            f"删除仓库 '{matched.get('Name')}'（id={matched.get('Id')}）后，依赖它的拉取会失败，须显式加 --yes"
        )
    require_ok(request(cfg, "DELETE", f"/api/registries/{matched.get('Id')}"))
    if args.json:
        dump_json({"deleted": matched.get("Id")})
    else:
        print(f"仓库 '{matched.get('Name')}'（id={matched.get('Id')}）已删除")
    return EXIT_OK

def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "registries", "镜像仓库凭据（增删查）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "仓库列表（凭据已脱敏）", common)
    lister.set_defaults(func=list_registries)

    creator = add_sub(sub, "create", "新增仓库凭据（口令从文件/环境变量读）", common)
    creator.add_argument("--name", required=True, help="显示名")
    creator.add_argument("--url", required=True, help="仓库地址，如 registry.example.com:5000")
    creator.add_argument("--type", type=int, default=3, help="类型：3=custom（默认），1=dockerhub，2=ghcr…")
    creator.add_argument("--registry-username", metavar="USER", help="仓库用户名（区别于全局 --username，那是 Portainer 登录名）")
    creator.add_argument("--password-file", metavar="PATH", help="从文件读口令（推荐）")
    creator.add_argument("--password-env", metavar="VAR", help="从该环境变量读口令，默认 $PORTAINER_REGISTRY_PASSWORD")
    creator.add_argument("--registry-base-url", metavar="URL", help="仓库 BaseURL（部分仓库需要；区别于全局 --base-url）")
    creator.add_argument("--management", action="store_true", help="同时用于管理 Swarm 镜像拉取")
    creator.add_argument("--dry-run", action="store_true", help="只打印将提交的请求体（口令已掩码）")
    creator.set_defaults(func=create_registry)

    deleter = add_sub(sub, "delete", "删除仓库凭据（需 --yes）", common)
    deleter.add_argument("registry", help="仓库 id 或名称")
    deleter.add_argument("--yes", action="store_true", help="确认删除")
    deleter.set_defaults(func=delete_registry)
