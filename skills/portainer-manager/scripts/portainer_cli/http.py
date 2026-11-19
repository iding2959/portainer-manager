"""HTTP 层：urllib 封装、JWT 登录缓存、统一错误格式。

只用标准库，是为了让 `python3 portainer.py` 在任何装了 python3 的机器上零依赖可跑。

三种 404 必须分清（实测，见 references/pitfalls.md）：
    "404 page not found"                 → 裸文本，Portainer 根本没有这个路由
    {"message":"Unable to find ..."}     → 路由在，Portainer 库里没有这个对象
    {"message":"service xxx not found"}  → 路由在，Docker 引擎没有这个资源
"""

import dataclasses
import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import ApiError, UsageError
from .config import Config


BODY_SNIPPET = 500


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """部分端点会 302（如 Grafana 跳转），调用方可能需要读 Location 而不是跟跳。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER_CACHE: Dict[Tuple[bool, bool], urllib.request.OpenerDirector] = {}


def _opener(insecure: bool, follow_redirects: bool) -> urllib.request.OpenerDirector:
    """按 (insecure, 是否跟跳) 缓存 opener；只省构造开销，连接每次新建。"""
    key = (insecure, follow_redirects)
    cached = _OPENER_CACHE.get(key)
    if cached is not None:
        return cached
    handlers: List[Any] = []
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if not follow_redirects:
        handlers.append(_NoRedirectHandler())
    opener = urllib.request.build_opener(*handlers)
    _OPENER_CACHE[key] = opener
    return opener


@dataclasses.dataclass(frozen=True)
class Response:
    method: str
    path: str
    status: int
    headers: Dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def is_bare_404(self) -> bool:
        """`404 page not found` 是裸文本，代表 Portainer 没有这个路由。"""
        return self.status == 404 and self.text.strip().lower().startswith("404 page not found")

    def json(self) -> Any:
        text = self.text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ApiError(
                f"{self.method} {self.path} -> 响应不是合法 JSON: {text[:BODY_SNIPPET]}"
            ) from exc


def _error_hint(status: int, response: Optional[Response] = None) -> str:
    if status == 401:
        return "（凭据无效或 JWT 过期：检查 --username/--password 或重新取 token）"
    if status == 403:
        return "（当前用户对该资源无权限：Portainer 按用户/团队/端点授权，不是 API 版本问题）"
    if status == 404 and response is not None and response.is_bare_404:
        return (
            "（Portainer 无此路由。注意 Docker 代理层只放行 GET 与 DELETE，"
            "PUT/POST/PATCH 一律返回 page not found——服务级更新请用 stacks update 或 docker service update）"
        )
    if status == 405:
        return "（该路径不支持此方法）"
    if status == 400:
        return "（请求体/参数未通过校验）"
    return ""


_SESSION_CACHE: Dict[Tuple[str, str], str] = {}


def login_token(cfg: Config) -> str:
    """用账号密码换 JWT。

    Portainer 的 JWT 默认 8 小时有效，进程内缓存一次即可；不落盘，
    避免把长期凭据写进磁盘。
    """
    if not cfg.username or not cfg.password:
        raise UsageError(
            "缺少账号密码：用 --username/--password 或 $PORTAINER_USERNAME/$PORTAINER_PASSWORD"
        )
    key = (cfg.base_url, cfg.username)
    cached = _SESSION_CACHE.get(key)
    if cached:
        return cached

    payload = json.dumps({"Username": cfg.username, "Password": cfg.password}).encode("utf-8")
    request = urllib.request.Request(cfg.base_url + "/api/auth", data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    try:
        with _opener(cfg.insecure, True).open(request, timeout=cfg.timeout) as raw:
            body = raw.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = " ".join(exc.read().decode("utf-8", "replace").split())[:BODY_SNIPPET]
        except OSError:
            pass
        # 三态必须分清（实测）：
        #   422 Invalid credentials  → 真的账号密码错了
        #   403 Access denied        → 登录限流被触发（连续登录太多次），等一会儿或改用 --token
        #   401                      → 见下方 resp 侧
        if exc.code == 403:
            raise ApiError(
                f"POST /api/auth -> HTTP 403: {detail or '(空响应体)'}"
                "（登录被拒：Portainer 对连续登录有速率限制。"
                "等几分钟再试，或用已拿到的 JWT 走 --token/$PORTAINER_TOKEN 绕过登录）"
            ) from exc
        raise ApiError(
            f"POST /api/auth -> HTTP {exc.code}: {detail or '(空响应体)'}"
            "（登录失败：检查 --username/--password；端口是 9000(HTTP) 或 9443(HTTPS)）"
        ) from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"POST /api/auth -> 网络错误: {exc.reason}") from exc

    try:
        data = json.loads(body.decode("utf-8", errors="replace") or "{}")
    except ValueError as exc:
        raise ApiError("POST /api/auth -> 响应不是合法 JSON（不是 Portainer？）") from exc
    token = data.get("jwt") if isinstance(data, dict) else None
    if not token:
        raise ApiError("POST /api/auth -> 响应里没有 jwt 字段")
    _SESSION_CACHE[key] = token
    return token


def auth_headers(cfg: Config) -> Dict[str, str]:
    """JWT 必须走 `Authorization: Bearer`。

    实测（2.39.4）：把同一个 JWT 放 `X-API-Key` 会得到
    `401 {"message":"Invalid JWT token"}`，而 Bearer 200。别照有些文档写 X-API-Key。
    """
    token = cfg.token or login_token(cfg)
    return {"Authorization": "Bearer " + token}


def request(
    cfg: Config,
    method: str,
    path: str,
    data: Any = None,
    follow_redirects: bool = True,
    raw_body: Optional[str] = None,
    timeout_override: Optional[float] = None,
) -> Response:
    """发一次请求并把非 2xx 也当响应返回，是否算错误由调用方决定。"""
    body = raw_body.encode("utf-8") if raw_body is not None else (
        json.dumps(data).encode("utf-8") if data is not None else None
    )
    request_obj = urllib.request.Request(cfg.base_url + path, data=body, method=method.upper())
    request_obj.add_header("Accept", "application/json")
    if body is not None:
        request_obj.add_header("Content-Type", "application/json")
    for name, value in auth_headers(cfg).items():
        request_obj.add_header(name, value)

    method_name = method.upper()
    try:
        with _opener(cfg.insecure, follow_redirects).open(
            request_obj, timeout=timeout_override or cfg.timeout
        ) as raw:
            status = getattr(raw, "status", None) or getattr(raw, "code", 0)
            headers = dict(raw.headers.items())
            payload = raw.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        headers = dict(exc.headers.items()) if exc.headers else {}
        try:
            payload = exc.read()
        except OSError:
            payload = b""
    except urllib.error.URLError as exc:
        raise ApiError(f"{method_name} {path} -> 网络错误: {exc.reason}") from exc
    except OSError as exc:
        raise ApiError(f"{method_name} {path} -> 网络错误: {exc}") from exc
    return Response(method_name, path, status, headers, payload)


def require_ok(response: Response) -> Response:
    if response.ok:
        return response
    snippet = " ".join(response.text.split())[:BODY_SNIPPET] or "(空响应体)"
    raise ApiError(
        f"{response.method} {response.path} -> HTTP {response.status}: {snippet}"
        f"{_error_hint(response.status, response)}"
    )


def get_json(cfg: Config, path: str) -> Any:
    return require_ok(request(cfg, "GET", path)).json()


def docker_path(cfg: Config, suffix: str) -> str:
    """Docker 代理路径：/api/endpoints/{id}/docker/{suffix}。"""
    return f"/api/endpoints/{cfg.endpoint_id}/docker/{suffix.lstrip('/')}"


def docker_get(cfg: Config, suffix: str) -> Any:
    return get_json(cfg, docker_path(cfg, suffix))


def as_list(payload: Any, path: str) -> List[Dict[str, Any]]:
    """Docker 代理与多数 Portainer 列表端点都返回裸数组；这里只做形状校验。"""
    if isinstance(payload, list):
        return list(payload)
    raise ApiError(
        f"GET {path} -> 响应不是数组: {json.dumps(payload, ensure_ascii=False)[:BODY_SNIPPET]}"
    )