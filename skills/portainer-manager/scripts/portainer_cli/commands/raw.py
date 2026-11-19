"""raw：任意端点逃生口。

写操作要求显式 --yes：raw 绕过所有前置校验（名称解析、危险操作确认），
只适合只读排查；真正需要写时请用对应的封装命令。
"""

import json
from typing import Any

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import request, require_ok
from ..output import dump_json


WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def run(cfg: Config, args: Any) -> int:
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else "/" + args.path

    data = None
    if args.data:
        try:
            raw = open(args.data, encoding="utf-8").read() if args.data.startswith("@") else args.data
        except OSError as exc:
            raise UsageError(f"--data 文件无法读取: {exc}")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise UsageError(f"--data 不是合法 JSON: {exc}")

    if method in WRITE_METHODS and not args.yes:
        raise UsageError(
            f"raw 的 {method} 会直接打 {path}，须显式 --yes；"
            "优先用封装命令（stacks/services），它们会做前置校验"
        )

    response = request(cfg, method, path, data=data)
    if args.show_status:
        print(f"HTTP {response.status}", flush=True)
    if not response.ok:
        require_ok(response)  # 抛出统一错误格式
    text = response.text.strip()
    if not text:
        return EXIT_OK
    try:
        dump_json(json.loads(text))
    except ValueError:
        print(text)
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "raw", "任意端点逃生口（写操作需 --yes）", common)
    parser.add_argument("method", help="HTTP 方法：GET/POST/PUT/DELETE…")
    parser.add_argument("path", help="路径，以 / 开头，如 /api/stacks")
    parser.add_argument("--data", help="请求体 JSON，或 @file.json")
    parser.add_argument("--yes", action="store_true", help="确认执行写操作（必需）")
    parser.add_argument("--show-status", action="store_true", help="额外打印 HTTP 状态码")
    parser.set_defaults(func=run)