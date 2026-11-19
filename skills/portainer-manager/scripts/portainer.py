#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portainer CE 管理 CLI（仅标准库，Python 3.9+）。

这里是入口：解析参数、按优先级装配配置、分发到 portainer_cli.commands 里的实现并统一
把异常转成退出码（0 成功 / 1 API 或网络错误 / 2 参数错误）。实现按职责拆到 portainer_cli/，
单文件不超过 500 行，Agent 按需读取更省 token。

常用示例：
    python3 portainer.py --base-url http://192.168.0.57:9000 health
    python3 portainer.py stacks list
    python3 portainer.py services list --stack n8n
    python3 portainer.py services scale n8n_n8n -r 2 --yes
    python3 portainer.py --json nodes list | jq '.[].Description.Hostname'
"""

import os
import sys
from typing import Optional, Sequence

from portainer_cli import ApiError, EXIT_API, EXIT_OK, EXIT_USAGE, UsageError
from portainer_cli.cli import build_parser, fill_defaults
from portainer_cli.config import resolve_config


def force_utf8() -> None:
    """Windows 控制台/重定向时中文可能按 GBK 写失败，这里只做尽力而为的修正。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def silence_stdout() -> None:
    """管道对端已关闭：把 stdout 指到 /dev/null，否则解释器退出刷缓冲会再报 BrokenPipe 并改坏退出码。"""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    force_utf8()
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = fill_defaults(parser.parse_args(raw_argv))
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        cfg = resolve_config(args)
        return handler(cfg, args) or EXIT_OK
    except UsageError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ApiError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return EXIT_API
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # 管道给 head/less 时会提前关闭 stdout，这不是错误
        silence_stdout()
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())