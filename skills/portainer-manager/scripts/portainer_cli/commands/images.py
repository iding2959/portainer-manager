"""images：镜像列表（按体积/悬空镜像排查磁盘）。"""

import datetime
import json
from typing import Any, Dict, List

from .. import ApiError, EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import as_list, docker_get, docker_path, request, require_ok
from ..output import dump_json, human_bytes, print_table, short_id


def _tags(item: dict) -> str:
    tags = item.get("RepoTags") or []
    return ",".join(tags) if tags else "<none>"


def _created(epoch: Any) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"



def delete_image(cfg: Config, args: Any) -> int:
    """删除镜像。三种引用形态都要考虑：

    - 被**运行中**容器引用 → Docker 409，必须先去停容器
    - 被**已停止**容器引用 → `--force` 才能删（会连带清掉容器引用）
    - 无引用 → 直接删

    `--force` 对应 Docker 的 `force=1`，不是"跳过确认"的意思，所以仍然要 `--yes`。
    """
    images = as_list(
        docker_get(cfg, "images/json"), f"/api/endpoints/{cfg.endpoint_id}/docker/images/json"
    )
    target = _resolve_image(images, args.image)
    image_id = target.get("Id") or ""
    tags = _tags(target)

    users = _containers_using(cfg, image_id)
    if users and not args.force:
        raise UsageError(
            f"镜像 '{tags}' 仍被 {len(users)} 个容器引用"
            f"（{'、'.join(u[0] for u in users[:3])}…）。\n"
            "确认要删：先删除这些容器，或加 --force（Docker 会拒绝删除运行中容器的镜像）。"
        )
    if not args.yes:
        scope = f"被 {len(users)} 个容器引用" if users else "无容器引用"
        raise UsageError(
            f"删除镜像 '{tags}'（{human_bytes(target.get('Size'))}，{scope}）不可恢复，"
            "须显式加 --yes"
        )

    path = docker_path(cfg, f"images/{image_id}")
    if args.force:
        path += "?force=1&noprune=0"
    try:
        require_ok(request(cfg, "DELETE", path))
    except ApiError as exc:
        text = str(exc)
        if "image is being used by running container" in text or "conflict" in text.lower():
            raise UsageError(
                f"镜像 '{tags}' 正被运行中的容器使用，Docker 拒绝删除：{exc}\n"
                "先停掉相关容器再删。"
            ) from exc
        raise

    if args.json:
        dump_json({"deleted": image_id, "tags": tags})
    else:
        print(f"镜像 '{tags}' 已删除")
    return EXIT_OK


def _containers_using(cfg: Config, image_id: str) -> List[List[Any]]:
    """反查哪些容器（含已停止）的 ImageID 指向该镜像。

    images/json 的 `.Containers` 字段在部分版本里不可靠，这里直接扫容器列表最稳。
    """
    path = f"/api/endpoints/{cfg.endpoint_id}/docker/containers/json?all=1"
    try:
        containers = as_list(docker_get(cfg, "containers/json?all=1"), path)
    except ApiError:
        return []
    wanted = (image_id or "").replace("sha256:", "")
    found: List[List[Any]] = []
    for item in containers:
        candidate = (item.get("ImageID") or "").replace("sha256:", "")
        if candidate and (candidate == wanted or candidate.startswith(wanted[:12])):
            name = ((item.get("Names") or ["-"])[0] or "-").lstrip("/")
            found.append([name, item.get("State")])
    return found


def _resolve_image(images: List[Dict[str, Any]], wanted: str) -> Dict[str, Any]:
    """按「完整 tag / tag 前缀 / 短 ID 前缀」匹配。镜像标识写法太多，三者都要认。"""
    needle = (wanted or "").strip()
    if not needle:
        raise UsageError("镜像标识不能为空")

    for item in images:
        if needle in (item.get("RepoTags") or []):
            return item
    short = needle.replace("sha256:", "")
    for item in images:
        candidate = (item.get("Id") or "").replace("sha256:", "")
        if candidate.startswith(short) and len(short) >= 6:
            return item
    for item in images:
        for tag in item.get("RepoTags") or []:
            if tag.endswith(":" + needle) or tag.startswith(needle):
                return item
    raise UsageError(f"找不到镜像 '{wanted}'（用 `images list` 看可用标识）")


def prune_images(cfg: Config, args: Any) -> int:
    """批量清理。

    默认只清**悬空**镜像（无 tag、无引用）；`--all` 会清掉所有无容器引用的镜像
    ——那等于把所有「备用的 tag 镜像」也删了，所以必须显式给。
    """
    # dry-run 是只读预览，不该要求 --yes；两者的用途完全不同
    if not args.yes and not args.dry_run:
        scope = "所有无容器引用的镜像（含带 tag 的备用镜像）" if args.all else "悬空镜像（无 tag）"
        raise UsageError(f"清理 {scope} 不可恢复，须显式加 --yes；先加 --dry-run 看会删哪些")

    images = as_list(
        docker_get(cfg, "images/json"), f"/api/endpoints/{cfg.endpoint_id}/docker/images/json"
    )
    candidates: List[Dict[str, Any]] = []
    for item in images:
        if _containers_using(cfg, item.get("Id") or ""):
            continue
        if args.all or not (item.get("RepoTags") or []):
            candidates.append(item)

    total = sum(int(item.get("Size") or 0) for item in candidates)
    rows = [[_tags(item), human_bytes(item.get("Size"))] for item in candidates]

    if args.dry_run or not candidates:
        if args.json:
            dump_json({"count": len(candidates), "bytes": total, "images": rows})
        else:
            print_table(["镜像", "大小"], rows, max_column_width=70)
            print(f"\n将清理 {len(candidates)} 个镜像，共可回收 {human_bytes(total)}")
        return EXIT_OK

    pruned, freed = 0, 0
    for item in candidates:
        try:
            require_ok(request(cfg, "DELETE", docker_path(cfg, f"images/{item.get('Id')}?force=1&noprune=0")))
            pruned += 1
            freed += int(item.get("Size") or 0)
        except ApiError:
            # 单条失败不阻断批量；真正有引用的会被 Docker 拒绝
            continue

    if args.json:
        dump_json({"pruned": pruned, "freed_bytes": freed})
    else:
        print(f"已清理 {pruned}/{len(candidates)} 个镜像，回收 {human_bytes(freed)}")
    return EXIT_OK


def pull_image(cfg: Config, args: Any) -> int:
    """拉取镜像。Docker 这个接口是流式 JSON（progress），这里把关键行打出来。

    `/images/create?fromImage=<repo>&tag=<tag>`：fromImage 是**不含 tag 的仓库名**，
    tag 单独传；带 tag 的完整串会被当成仓库名导致失败。
    """
    ref = args.image
    repo, _, tag = ref.rpartition(":")
    if not repo:
        repo, tag = ref, "latest"
    elif "/" in tag:  # 形如 localhost:5000/foo 没有 tag
        repo, tag = ref, "latest"
    path = docker_path(cfg, f"images/create?fromImage={repo}&tag={tag}")

    if args.dry_run:
        print(f"将 POST {path}")
        return EXIT_OK

    response = require_ok(request(cfg, "POST", path, timeout_override=args.timeout))
    lines = [line for line in response.text.splitlines() if line.strip()]
    if args.json:
        print(json.dumps([json.loads(line) for line in lines if line.startswith("{")], ensure_ascii=False))
        return EXIT_OK

    last = ""
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("status") or event.get("error") or ""
        if message:
            last = message
    print(f"已拉取 {repo}:{tag}" + (f"（最后状态：{last}）" if last else ""))
    return EXIT_OK

def list_images(cfg: Config, args: Any) -> int:
    images = as_list(docker_get(cfg, "images/json"), f"/api/endpoints/{cfg.endpoint_id}/docker/images/json")

    if args.search:
        needle = args.search.lower()
        images = [item for item in images if needle in _tags(item).lower()]
    if args.dangling:
        images = [item for item in images if not (item.get("RepoTags") or [])]

    images.sort(key=lambda item: item.get("Size") or 0, reverse=True)
    if args.limit:
        images = images[: args.limit]

    rows: List[List[Any]] = []
    for item in images:
        rows.append([
            short_id(item.get("Id"), 19),
            _tags(item),
            human_bytes(item.get("Size")),
            _created(item.get("Created")),
            "yes" if (item.get("Containers") or 0) > 0 else "-",
        ])

    if args.json:
        dump_json(images)
        return EXIT_OK
    print_table(
        ["镜像ID", "标签", "大小", "创建时间", "在用"],
        rows,
        max_column_width=64,
    )
    print(f"\n共 {len(rows)} 个镜像（按大小降序）")
    return EXIT_OK


def register(subparsers: Any, common: Any) -> None:
    parser = add_sub(subparsers, "images", "镜像（增删查 + 清理 + 拉取）", common)
    sub = parser.add_subparsers(dest="action", metavar="<动作>")

    lister = add_sub(sub, "list", "镜像列表", common)
    lister.add_argument("--search", metavar="S", help="按镜像标签模糊过滤")
    lister.add_argument("--dangling", action="store_true", help="只看无标签悬空镜像")
    lister.add_argument("--limit", type=int, help="只显示最大的 N 个")
    lister.set_defaults(func=list_images)

    deleter = add_sub(sub, "delete", "删除单个镜像（需 --yes）", common)
    deleter.add_argument("image", help="完整 tag、tag 名或短 ID 前缀")
    deleter.add_argument("--force", action="store_true", help="强制删除（可清掉指向它的已停止容器）")
    deleter.add_argument("--yes", action="store_true", help="确认删除（不可恢复）")
    deleter.set_defaults(func=delete_image)

    pruner = add_sub(sub, "prune", "批量清理无引用镜像（需 --yes）", common)
    pruner.add_argument("--all", action="store_true", help="连有 tag 的备用镜像一起清（默认只清悬空）")
    pruner.add_argument("--dry-run", action="store_true", help="只列出会删哪些")
    pruner.add_argument("--yes", action="store_true", help="确认清理（不可恢复）")
    pruner.set_defaults(func=prune_images)

    puller = add_sub(sub, "pull", "拉取镜像", common)
    puller.add_argument("image", help="完整引用，如 nginx:1.27-alpine")
    puller.add_argument("--dry-run", action="store_true", help="只打印将请求的路径")
    puller.set_defaults(func=pull_image)
