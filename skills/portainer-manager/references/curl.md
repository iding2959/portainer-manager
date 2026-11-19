# curl / jq 片段（脱离 CLI 时用）

```bash
export PT=http://192.168.0.57:9000
export JWT=$(curl -s -X POST $PT/api/auth \
  -H 'Content-Type: application/json' \
  -d '{"Username":"admin","Password":"'"$PORTAINER_PASSWORD"'"}' | jq -r .jwt)
export H="Authorization: Bearer $JWT"
```

> 密码只从环境变量来，别写进脚本或历史文件（`history` 会留痕，用 `read -s` 或环境变量）。

## 1. 基础探针

```bash
# Portainer 版本（免鉴权）
curl -s $PT/api/system/status | jq

# Docker 引擎
curl -s -H "$H" $PT/api/endpoints/1/docker/version | jq '{Version,ApiVersion,MinAPIVersion}'
curl -s -H "$H" $PT/api/endpoints/1/docker/info \
  | jq '{ServerVersion, Containers, ContainersRunning, Images, NCPU, MemTotal}'

# Swarm 集群
curl -s -H "$H" $PT/api/endpoints/1/docker/swarm \
  | jq '{ID, Name:.Spec.Name, CreatedAt, Version:.Version.Index}'
```

## 2. 栈

```bash
# 全部栈（注意 Status 是 1/2，不是 true/false）
curl -s -H "$H" $PT/api/stacks \
  | jq -r '.[] | "\(.Id)\t\(.Name)\ttype=\(.Type)\tstatus=\(.Status)"'

# 只活着的
curl -s -H "$H" $PT/api/stacks | jq -r '.[] | select(.Status==1) | .Name'

# 栈的 compose 原文
curl -s -H "$H" $PT/api/stacks/3 | jq -r '.Name'
curl -s -H "$H" $PT/api/stacks/3/file | jq -r '.StackFileContent'

# 按名字找栈 id
curl -s -H "$H" $PT/api/stacks | jq -r '.[] | select(.Name=="n8n") | .Id'
```

## 3. 服务（只读）

```bash
# 服务清单：栈 / 全名 / 副本 / 镜像
curl -s -H "$H" $PT/api/endpoints/1/docker/services \
  | jq -r '.[] | [
      (.Spec.Labels["com.docker.stack.namespace"] // "-"),
      .Spec.Name,
      (.Spec.Mode.Replicated.Replicas // "global"),
      (.Spec.TaskTemplate.ContainerSpec.Image | split("@")[0])
    ] | @tsv'

# 某个服务的完整定义
curl -s -H "$H" $PT/api/endpoints/1/docker/services | jq -r '.[]|select(.Spec.Name=="n8n_n8n")|.ID' \
  | xargs -I{} curl -s -H "$H" $PT/api/endpoints/1/docker/services/{} | jq .

# 服务版本号（写操作要用）
curl -s -H "$H" $PT/api/endpoints/1/docker/services | jq -r '.[]|select(.Spec.Name=="n8n_n8n")|.Version.Index'

# 端口映射
curl -s -H "$H" $PT/api/endpoints/1/docker/services \
  | jq -r '.[] | select(.Endpoint.Ports != null) | "\(.Spec.Name)\t" + ([.Endpoint.Ports[]|"\(.PublishedPort)->\(.TargetPort)"]|join(","))'
```

## 4. 任务与故障排查

```bash
# 只要当前任务（过滤掉历史 shutdown）
curl -s -H "$H" $PT/api/endpoints/1/docker/tasks \
  | jq -r '.[] | select(.DesiredState!="shutdown") | "\(.Status.State)\t\(.ID[0:12])\t\(.Slot)"'

# 失败类任务及其原因
curl -s -H "$H" $PT/api/endpoints/1/docker/tasks \
  | jq -r '.[] | select(.Status.State|test("failed|rejected|orphaned")) 
           | "\(.Status.State)\t\(.ServiceID[0:12])\t\(.Status.Message)"'

# 状态分布
curl -s -H "$H" $PT/api/endpoints/1/docker/tasks | jq -r '.[].Status.State' | sort | uniq -c

# 某服务重启过几次（同一 Slot 有多个任务 = 有重启）
curl -s -H "$H" $PT/api/endpoints/1/docker/tasks \
  | jq -r --arg sid "$SID" '.[]|select(.ServiceID==$sid)|"\(.Slot)\t\(.Status.State)\t\(.CreatedAt)"' | sort
```

## 5. 容器与日志

```bash
# 当前容器（含栈归属）
curl -s -H "$H" $PT/api/endpoints/1/docker/containers/json \
  | jq -r '.[] | "\(.Names[0])\t\(.State)\t\(.Labels["com.docker.stack.namespace"] // "-")\t\(.Status)"'

# 某个服务的容器 id
CID=$(curl -s -H "$H" $PT/api/endpoints/1/docker/containers/json \
  | jq -r '.[]|select(.Labels["com.docker.swarm.service.name"]=="n8n_n8n")|.Id' | head -1)

# 日志（末尾部分；注意会混有流复用帧头，用 tr 去掉 NUL）
curl -s -H "$H" "$PT/api/endpoints/1/docker/containers/$CID/logs?stdout=1&stderr=1&tail=200" \
  | tr -d '\000'
```

## 6. 网络 / 卷 / 镜像

```bash
# 网络（只看 swarm 范围）
curl -s -H "$H" $PT/api/endpoints/1/docker/networks \
  | jq -r '.[]|select(.Scope=="swarm")|"\(.Name)\t\(.Driver)\t\((.IPAM.Config[0].Subnet) // "-")"'

# 某网络接了哪些容器
curl -s -H "$H" $PT/api/endpoints/1/docker/networks \
  | jq -r '.[]|select(.Name=="sf-net")|.Containers|to_entries[]|"\(.value.Name)\t\(.value.IPv4Address)"'

# 卷（注意是 .Volumes 包裹）
curl -s -H "$H" $PT/api/endpoints/1/docker/volumes | jq -r '.Volumes[].Name'

# 镜像按体积降序
curl -s -H "$H" $PT/api/endpoints/1/docker/images/json \
  | jq -r 'sort_by(-.Size)[] | "\((.Size/1073741824*100|round)/100)GiB\t\(.RepoTags // ["<none>"] | join(","))"' \
  | head -20
```

## 7. 更新栈（最常用的写操作）

```bash
# 1) 取原文
curl -s -H "$H" $PT/api/stacks/3/file | jq -r '.StackFileContent' > /tmp/stack.yml
# 2) 本地编辑 /tmp/stack.yml
# 3) 整体回填（必须是完整内容，不是 patch）
jq -Rs '{StackFileContent: ., Env: [], Prune: false, RepullImageAndRedeploy: false}' /tmp/stack.yml > /tmp/body.json
curl -s -X PUT -H "$H" -H 'Content-Type: application/json' \
  --data @/tmp/body.json "$PT/api/stacks/3?endpointId=1" | jq '{Id,Name,Status}'
```

## 8. 整栈启停

```bash
curl -s -X POST -H "$H" "$PT/api/stacks/3/stop?endpointId=1"  -w '%{http_code}\n'
curl -s -X POST -H "$H" "$PT/api/stacks/3/start?endpointId=1" -w '%{http_code}\n'
```

无请求体；`endpointId` 必填。

## 9. 全量巡检一行流

```bash
{
  echo "== 栈";   curl -s -H "$H" $PT/api/stacks | jq -r '.[]|"\(.Id)\t\(.Name)\t\(.Status)"'
  echo "== 服务"; curl -s -H "$H" $PT/api/endpoints/1/docker/services | jq -r '.[]|"\(.Spec.Name)\t\(.Spec.Mode.Replicated.Replicas // "global")"'
  echo "== 任务"; curl -s -H "$H" $PT/api/endpoints/1/docker/tasks | jq -r '.[]|select(.DesiredState!="shutdown")|"\(.Status.State)"' | sort | uniq -c
  echo "== 节点"; curl -s -H "$H" $PT/api/endpoints/1/docker/nodes | jq -r '.[]|"\(.Description.Hostname)\t\(.Status.State)\t\(.Spec.Availability)"'
}
```

## 10. 用 CLI 替代这些 curl

CLI 已封装上述绝大多数场景，优先用它（自动处理鉴权、表格渲染、危险操作确认）：

```bash
python3 scripts/portainer.py health
python3 scripts/portainer.py stacks list
python3 scripts/portainer.py services list --stack n8n
python3 scripts/portainer.py tasks list --failed-only
python3 scripts/portainer.py stacks update n8n --set-image n8n=img:tag --dry-run
python3 scripts/portainer.py --json nodes list | jq -r '.[].Description.Hostname'
```