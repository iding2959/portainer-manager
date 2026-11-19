"""tasks：Swarm 任务（服务的调度实例）与故障排查。"""

from typing import Any, Dict, List

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import as_list, docker_get
from ..output import dump_json, print_table, short_id
from ..resolve import all_services, resolve_service


FAILED_STATES = ("failed", "rejected", "orphaned", "remove")


def _service_names(cfg: Config) -> Dict[str, str]:
    return {service.get("ID") or "": (service.get("Spec") or {}).get("Name") or "" for service in all_services(cfg)}


def _task_rows(tasks: List[Dict[str, Any]], names: Dict[str, str]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for task in tasks:
        status = task.get("Status") or {}
        container = status.get("ContainerStatus") or {}
        rows.append([
            short_id(task.get("ID")),
            names.get(task.get("ServiceID") or "", short_id(task.get("ServiceID"))),
            task.get("Slot"),
            status.get("State"),
            task.get("DesiredState"),
            short_id(task.get("NodeID")),
            status.get("Message") or "-",
            container.get("ExitCode"),
            (status.get("Timestamp") or "")[:19],
        ])
    return rows


def list_tasks(cfg: Config, args: Any) -> int:
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/tasks"
    tasks = as_list(docker_get(cfg, "tasks"), path)

    if args.service:
        service = resolve_service(cfg, args.service)
        tasks = [task for task in tasks if task.get("ServiceID") == service.get("ID")]
    if args.state:
        tasks = [
            task for task in tasks
            if ((task.get("Status") or {}).get("State") or "") == args.state
        ]
    if args.failed_only:
        tasks = [
            task for task in tasks
            if ((task.get("Status") or {}).get("State") or "") in FAILED_STATES
        ]
    if not args.all:
        tasks = [task for task in tasks if (task.get("DesiredState") or "") != "shutdown"]

    names = _service_names(cfg)
    if args.json:
        dump_json(tasks)
        return EXIT_OK
    rows = _task_rows(tasks, names)
    print_table(
        ["任务ID", "服务", "槽位", "状态", "期望", "节点ID", "消息", "退出码", "时间"],
        rows,
        max_column_width=44,
    )
    print(f"\n共 {len(rows)} 个任务" + ("" if args.all else "（已隐藏 shutdown；--all 看全部）"))
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "tasks", "Swarm 任务（排查 failed/重复重启）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "任务列表", common)
    lister.add_argument("--service", metavar="S", help="只看某个服务的任务")
    lister.add_argument("--state", metavar="S", help="按状态过滤（running/failed/shutdown…）")
    lister.add_argument("--failed-only", action="store_true", help="只看失败类任务")
    lister.add_argument("--all", action="store_true", help="含 shutdown 的历史任务")
    lister.set_defaults(func=list_tasks)