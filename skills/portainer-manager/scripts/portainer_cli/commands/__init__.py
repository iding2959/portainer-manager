"""命令模块集中注册处。

每个模块暴露 `register(subparsers, common)`，由 cli.build_parser 依次调用。
"""

__all__ = [
    "configs",
    "containers",
    "health",
    "images",
    "networks",
    "nodes",
    "raw",
    "registries",
    "resources",
    "services",
    "stacks",
    "tasks",
    "users",
    "volumes",
]