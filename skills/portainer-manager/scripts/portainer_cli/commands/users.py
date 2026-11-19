"""users：Portainer 用户与团队（权限排查）。"""

from typing import Any, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import get_json
from ..output import dump_json, print_table


ROLE = {1: "admin", 2: "user"}


def list_users(cfg: Config, args: Any) -> int:
    users = get_json(cfg, "/api/users") or []
    rows: List[List[Any]] = []
    for item in users:
        authorizations = item.get("EndpointAuthorizations") or []
        rows.append([
            item.get("Id"),
            item.get("Username"),
            ROLE.get(item.get("Role"), item.get("Role")),
            len(item.get("TeamMemberships") or []),
            f"{len(authorizations)} 个端点授权",
            item.get("LastCheckInDate") or "-",
        ])
    if args.json:
        dump_json(users)
        return EXIT_OK
    print_table(
        ["ID", "用户名", "角色", "团队数", "授权", "上次登录"],
        rows,
        max_column_width=40,
    )
    print(f"\n共 {len(rows)} 个用户（role 1=admin 2=user；用户级权限不在此改动）")
    return EXIT_OK


def list_teams(cfg: Config, args: Any) -> int:
    teams = get_json(cfg, "/api/teams") or []
    if args.json:
        dump_json(teams)
        return EXIT_OK
    rows = [
        [
            item.get("Id"),
            item.get("Name"),
            len(item.get("TeamMemberships") or []),
        ]
        for item in teams
    ]
    print_table(["ID", "团队名", "成员数"], rows, max_column_width=40)
    print(f"\n共 {len(rows)} 个团队")
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "users", "Portainer 用户", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")
    lister = add_sub(sub, "list", "用户列表", common)
    lister.set_defaults(func=list_users)

    teams = add_sub(subparsers, "teams", "Portainer 团队", common)
    sub2 = teams.add_subparsers(dest="action", metavar="<动作>")
    tlister = add_sub(sub2, "list", "团队列表", common)
    tlister.set_defaults(func=list_teams)