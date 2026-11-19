"""argparse 组装与分发骨架。

build_parser 只负责「按顺序调用各命令模块的 register()」，具体参数定义留在各命令模块，
避免 cli.py 变成新的巨石文件。
"""

import argparse
from typing import List, Sequence

from .commands import (
    containers,
    health,
    images,
    networks,
    nodes,
    raw,
    registries,
    resources,
    services,
    stacks,
    tasks,
    users,
    volumes,
)
from .config import DEFAULT_BASE_URL, DEFAULT_CONFIG_PATH, DEFAULT_ENDPOINT_ID, DEFAULT_TIMEOUT


COMMAND_MODULES = (
    health,
    stacks,
    services,
    nodes,
    tasks,
    containers,
    networks,
    volumes,
    images,
    resources,
    users,
    registries,
    raw,
)


def build_common_parser() -> argparse.ArgumentParser:
    """全局参数用 SUPPRESS 默认值：这样「子命令前」的值不会被「子命令后」的默认值覆盖。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-url",
        metavar="URL",
        default=argparse.SUPPRESS,
        help=f"Portainer 地址；优先级 参数 > $PORTAINER_BASE_URL > 配置文件 > {DEFAULT_BASE_URL}",
    )
    common.add_argument(
        "--username",
        metavar="USER",
        default=argparse.SUPPRESS,
        help="Portainer 用户名（与 --password 同用，换 JWT）",
    )
    common.add_argument(
        "--password",
        metavar="PASS",
        default=argparse.SUPPRESS,
        help="Portainer 密码（不落盘、不写日志；也可用 $PORTAINER_PASSWORD）",
    )
    common.add_argument(
        "--token",
        metavar="JWT",
        default=argparse.SUPPRESS,
        help="直接给 JWT（优先于账号密码；也可用 $PORTAINER_TOKEN）",
    )
    common.add_argument(
        "--endpoint-id",
        type=int,
        metavar="N",
        default=argparse.SUPPRESS,
        help=f"Portainer 环境(Endpoint) id，Docker 透传用；默认 {DEFAULT_ENDPOINT_ID}",
    )
    common.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}，内容 {{\"base_url\":...,\"username\":...}}",
    )
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出原始 JSON（保证可被 jq 解析）",
    )
    common.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        default=argparse.SUPPRESS,
        help=f"请求超时秒数，默认 {int(DEFAULT_TIMEOUT)}",
    )
    common.add_argument(
        "--insecure",
        action="store_true",
        default=argparse.SUPPRESS,
        help="关闭 TLS 证书校验（自签证书的 9443）",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = build_common_parser()
    parser = argparse.ArgumentParser(
        prog="portainer.py",
        description="Portainer CE 管理 CLI（Docker Swarm 服务管理，仅标准库）",
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  portainer.py health\n"
            "  portainer.py stacks list\n"
            "  portainer.py services list --stack n8n\n"
            "  portainer.py services scale n8n_n8n -r 2 --yes\n"
            "  portainer.py --json nodes list | jq '.[].Description.Hostname'\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="resource", metavar="<资源>")
    for module in COMMAND_MODULES:
        module.register(subparsers, common)
    return parser


def fill_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """SUPPRESS 只挡住「子命令前」的默认值，子命令自己没设的属性这里补齐。"""
    if not hasattr(args, "json"):
        args.json = False
    return args


def main_argv_hint(argv: Sequence[str]) -> List[str]:
    """给错误信息用：把参数原样返回（便于日志复现）。"""
    return list(argv)