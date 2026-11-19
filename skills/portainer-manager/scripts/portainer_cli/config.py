"""配置来源与优先级：命令行参数 > 环境变量 > 配置文件 > 默认值。

密钥只从这三处读，绝不写进代码：脚本要能公开发布，硬编码口令等于泄漏。
"""

import argparse
import dataclasses
import json
import os
from typing import Any, Dict, Optional

from . import UsageError


DEFAULT_BASE_URL = "https://127.0.0.1:9443"
DEFAULT_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".portainer", "config.json")
DEFAULT_TIMEOUT = 30.0
DEFAULT_ENDPOINT_ID = 1


@dataclasses.dataclass(frozen=True)
class Config:
    base_url: str
    token: Optional[str]
    username: Optional[str]
    password: Optional[str]
    timeout: float
    insecure: bool
    endpoint_id: int


def load_config_file(path: str, explicit: bool) -> Dict[str, Any]:
    """读取配置文件；显式指定的路径不存在时视为参数错误，隐式默认路径缺失则忽略。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        if explicit:
            raise UsageError(f"配置文件不存在: {path}")
        return {}
    except OSError as exc:
        raise UsageError(f"配置文件无法读取: {path}: {exc}")
    except ValueError as exc:
        raise UsageError(f"配置文件不是合法 JSON: {path}: {exc}")
    if not isinstance(data, dict):
        raise UsageError(f"配置文件顶层必须是 JSON 对象: {path}")
    return data


def normalize_base_url(value: str) -> str:
    """容忍用户只写 host:port；末尾斜杠必须去掉，否则拼路径会变成 //api/...。"""
    url = (value or "").strip()
    if not url:
        return DEFAULT_BASE_URL
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    return url.rstrip("/")


def _pick_str(*candidates: Any) -> Optional[str]:
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_config(args: argparse.Namespace) -> Config:
    explicit_config = getattr(args, "config", None)
    config_path = explicit_config or DEFAULT_CONFIG_PATH
    file_values = load_config_file(config_path, explicit=bool(explicit_config))

    base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("PORTAINER_BASE_URL")
        or file_values.get("base_url")
        or DEFAULT_BASE_URL
    )

    # 三种凭据来源，优先级：token > 账号密码（两条路都只是在换同一个 JWT）
    token = _pick_str(
        getattr(args, "token", None),
        os.environ.get("PORTAINER_TOKEN"),
        file_values.get("token"),
    )
    username = _pick_str(
        getattr(args, "username", None),
        os.environ.get("PORTAINER_USERNAME"),
        file_values.get("username"),
    )
    password = _pick_str(
        getattr(args, "password", None),
        os.environ.get("PORTAINER_PASSWORD"),
        file_values.get("password"),
    )
    if password and not username:
        raise UsageError("给了 --password 但没给 --username（账号密码登录两者都要）")
    if username and not password:
        raise UsageError("给了 --username 但没给 --password（账号密码登录两者都要）")
    if not (token or (username and password)):
        raise UsageError(
            "缺少凭据：用 --username/--password、$PORTAINER_USERNAME/$PORTAINER_PASSWORD、"
            "$PORTAINER_TOKEN，或配置文件 ~/.portainer/config.json"
        )

    timeout = getattr(args, "timeout", DEFAULT_TIMEOUT)
    if timeout is None or timeout <= 0:
        raise UsageError(f"--timeout 必须大于 0，收到: {timeout}")

    endpoint_id = getattr(args, "endpoint_id", None)
    if endpoint_id is None:
        endpoint_id = os.environ.get("PORTAINER_ENDPOINT_ID") or file_values.get("endpoint_id")
    if endpoint_id is None:
        endpoint_id = DEFAULT_ENDPOINT_ID
    try:
        endpoint_id = int(endpoint_id)
    except (TypeError, ValueError):
        raise UsageError(f"--endpoint-id 必须是整数，收到: {endpoint_id!r}")

    return Config(
        base_url=normalize_base_url(base_url),
        token=token,
        username=username,
        password=password,
        timeout=float(timeout),
        insecure=bool(getattr(args, "insecure", False)),
        endpoint_id=endpoint_id,
    )