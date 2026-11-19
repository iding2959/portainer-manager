"""registries：镜像仓库凭据（只读；凭据内容不打印）。"""

from typing import Any, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import get_json
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


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "registries", "镜像仓库凭据", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")
    lister = add_sub(sub, "list", "仓库列表（凭据已脱敏）", common)
    lister.set_defaults(func=list_registries)