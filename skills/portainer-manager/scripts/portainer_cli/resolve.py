"""名称 → ID 解析。

Portainer 没有「按名字查服务」的端点，Docker 代理也只给全量列表，所以只能在客户端
过滤。服务名有三种可能写法，必须都认：

    n8n_n8n     全名（Docker 里的真实 Spec.Name）
    n8n         「栈_服务」里的服务简称
    stack:n8n   显式限定栈

解析失败时把可用候选列出来，让报错本身能指导下一步。
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ApiError, UsageError
from .config import Config
from .http import as_list, docker_get
from .output import stack_of


MAX_NAME_LIST = 20


def all_services(cfg: Config) -> List[Dict[str, Any]]:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/services"
    return as_list(docker_get(cfg, "services"), path)


def all_stacks(cfg: Config) -> List[Dict[str, Any]]:
    from .http import get_json

    return as_list(get_json(cfg, "/api/stacks"), "/api/stacks")


def service_name(service: Dict[str, Any]) -> str:
    return (service.get("Spec") or {}).get("Name") or ""


def match_service(services: Sequence[Dict[str, Any]], wanted: str) -> Optional[Dict[str, Any]]:
    """三级匹配：全名精确 > 「栈:服务」限定 > 栈内简称唯一。"""
    wanted = (wanted or "").strip()
    if not wanted:
        return None

    for service in services:
        if service_name(service) == wanted:
            return service

    if ":" in wanted:
        stack_part, _, short_part = wanted.partition(":")
        candidates = [
            service
            for service in services
            if stack_of(service) == stack_part
            and service_name(service) == f"{stack_part}_{short_part}"
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise UsageError(f"服务 '{wanted}' 在栈 '{stack_part}' 内不唯一")
        return None

    candidates = [
        service
        for service in services
        if stack_of(service) != "-" and service_name(service) == f"{stack_of(service)}_{wanted}"
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = ", ".join(service_name(item) for item in candidates)
        raise UsageError(f"简称 '{wanted}' 命中多个服务，请用全名：{names}")
    return None


def names_hint(services: Sequence[Dict[str, Any]], limit: int = MAX_NAME_LIST) -> str:
    names = [service_name(service) for service in services][:limit]
    return "、".join(names) if names else "（无）"


def similar_services(services: Sequence[Dict[str, Any]], wanted: str, limit: int = 5) -> List[str]:
    """找出「用户可能想输入的那几个」服务，用于报错时的引导。

    简称规则是「服务全名 == 栈名_简称」，所以当栈名或服务名本身含连字符时，直觉上的简称
    会和这条规则对不上（例如栈 `cloudflared` 下的服务 `cloudflared_cloudflare-tunnel`，
    简称既不是 `cloudflared` 也不是 `cloudflare-tunnel`）。此时只列全量名字帮助有限，
    要直接把「栈:服务」这种确定能用的写法摆出来。

    判据：服务全名包含用户输入、或某个栈名包含用户输入，就算相近。
    """
    needle = (wanted or "").strip().lower()
    if not needle:
        return []
    scored: List[tuple[int, str]] = []
    for service in services:
        full = service_name(service)
        namespace = stack_of(service)
        full_l, ns_l = full.lower(), (namespace or "").lower()
        if needle in full_l:
            # 命中「服务全名」比命中「仅栈名」更贴近用户意图，排在前面
            scored.append((0, _address(service, full, namespace)))
        elif namespace != "-" and (needle in ns_l or ns_l in needle):
            scored.append((1, _address(service, full, namespace)))
    scored.sort(key=lambda item: item[0])
    seen: List[str] = []
    for _, text in scored:
        if text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def _address(service: Dict[str, Any], full: str, namespace: str) -> str:
    """给出一条可以直接粘贴的写法：`栈:服务简称`（比全名短，且一定能解析）。"""
    if namespace and namespace != "-" and full.startswith(namespace + "_"):
        return f"{namespace}:{full[len(namespace) + 1:]}"
    return full


def resolve_service(cfg: Config, wanted: str) -> Dict[str, Any]:
    services = all_services(cfg)
    found = match_service(services, wanted)
    if found is not None:
        return found

    hints = similar_services(services, wanted)
    if hints:
        # 有相近候选时，直接把能用的写法给出，省掉用户再猜一轮
        raise ApiError(
            f"找不到服务 '{wanted}'。相近的有：{'、'.join(hints)}\n"
            f"提示：栈名或服务名含连字符时，简称规则（服务全名 = 栈名_简称）可能和直觉对不上，"
            f"用 `栈:服务` 写法最稳。"
        )
    raise ApiError(f"找不到服务 '{wanted}'（可用：{names_hint(services)}）")


def resolve_stack(cfg: Config, wanted: str) -> Dict[str, Any]:
    """栈既接受数字 id，也接受名字。"""
    stacks = all_stacks(cfg)
    text = (wanted or "").strip()
    if text.isdigit():
        for stack in stacks:
            if str(stack.get("Id")) == text:
                return stack
    for stack in stacks:
        if stack.get("Name") == text:
            return stack
    names = "、".join(f"{item.get('Id')}:{item.get('Name')}" for item in stacks[:MAX_NAME_LIST])
    raise ApiError(f"找不到栈 '{wanted}'（可用：{names or '（无）'}）")


def items_grouped_by_stack(
    cfg: Config,
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """按栈归组服务，供 `services list` 的树状输出使用。"""
    groups: Dict[str, Dict[str, Any]] = {}
    for service in all_services(cfg):
        namespace = stack_of(service)
        group = groups.setdefault(
            namespace,
            {"Spec": {"Name": namespace, "Labels": {"com.docker.stack.namespace": namespace}}},
        )
        group.setdefault("_services", [])
        group["_services"] = group.get("_services", []) + [service]  # type: ignore[list-item]
    ordered = []
    for namespace in sorted(groups):
        group = groups[namespace]
        ordered.append((group, group.get("_services") or []))  # type: ignore[arg-type]
    return ordered


def filter_services(
    services: Sequence[Dict[str, Any]], stack: Optional[str]
) -> List[Dict[str, Any]]:
    if not stack:
        return list(services)
    return [service for service in services if stack_of(service) == stack]


def resolve_task_containers(
    cfg: Config, service_id: str, include_shutdown: bool
) -> List[Dict[str, Any]]:
    """取某服务的任务（含历史）；只保留最近 slot 的活跃任务由调用方决定。"""
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/tasks"
    tasks = as_list(docker_get(cfg, "tasks"), path)
    matched = [task for task in tasks if task.get("ServiceID") == service_id]
    if not include_shutdown:
        matched = [
            task
            for task in matched
            if (task.get("DesiredState") or "") != "shutdown"
        ]
    return matched