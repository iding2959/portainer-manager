# 对象字段与枚举（Portainer CE 2.39.4 / Docker 29.6.1）

## Portainer Stack（`portainer.Stack`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `Id` | int | 栈 id |
| `Name` | string | 栈名（**服务名前缀就是这个**） |
| `Type` | int | **1=Swarm**，2=Compose（standalone），3=Kubernetes |
| `EndpointId` | int | 所属环境 id |
| `SwarmId` | string | Swarm 集群 id |
| `EntryPoint` | string | compose 入口文件名（本环境统一 `docker-compose.yml`） |
| `Env` | `[{name,value}]` | 栈环境变量 |
| `Status` | int | **1=active，2=inactive**（不是 0/1） |
| `ProjectPath` | string | 容器内路径 `/data/compose/{id}` |
| `CreationDate` / `UpdateDate` | epoch 秒 | `UpdateDate=0` 表示从未更新过 |
| `CreatedBy` / `UpdatedBy` | string | 用户名 |
| `AdditionalFiles` | array\|null | 附加文件（本环境全 null） |
| `AutoUpdate` | object\|null | 自动更新配置 |
| `GitConfig` | object\|null | Git 配置；**null 表示不是 Git 栈** |
| `Option` | `{Prune,HelmAtomic}` | 部署选项 |
| `ResourceControl` | object | 访问控制 |
| `FromAppTemplate` | bool | 是否由模板创建 |
| `Namespace` | string | Kubernetes 命名空间（Swarm 为空串） |

## Swarm Service（`docker service inspect`）

| 字段 | 说明 |
|---|---|
| `ID` | 服务 id（25 字符 base36） |
| `Version.Index` | **更新服务时的乐观锁版本** |
| `CreatedAt` / `UpdatedAt` | RFC3339 |
| `Spec.Name` | 服务全名 `<栈>_<服务>` |
| `Spec.Labels["com.docker.stack.namespace"]` | 栈名（**无栈服务没有此键**） |
| `Spec.Labels["com.docker.stack.image"]` | 栈创建时的镜像 |
| `Spec.Mode` | `{Replicated:{Replicas:N}}` 或 `{Global:{}}` |
| `Spec.TaskTemplate.ContainerSpec.Image` | 镜像（常带 `@sha256:`） |
| `Spec.TaskTemplate.ContainerSpec.Env` | 环境变量数组（`KEY=VALUE`） |
| `Spec.TaskTemplate.ContainerSpec.Mounts` | 挂载（`Type: bind/volume`、`Source`、`Target`） |
| `Spec.TaskTemplate.Placement.Constraints` | 如 `["node.role == manager"]` |
| `Spec.TaskTemplate.RestartPolicy` | `{Condition, MaxAttempts}` |
| `Spec.Networks` | `[{Target:<网络id>, Aliases:[...]}]` |
| `Endpoint.Ports` | `[{Protocol, PublishMode, PublishedPort, TargetPort}]` |
| `UpdateStatus` | `{State, Message, StartedAt, CompletedAt}` |

## Swarm Task

| 字段 | 说明 |
|---|---|
| `ID` | 任务 id |
| `ServiceID` | 所属服务 |
| `NodeID` | 所在节点（**是完整 25 字符，不是短 id**） |
| `Slot` | 副本槽位（1-based） |
| `DesiredState` | `running` / `shutdown` / `accepted` 等 |
| `Status.State` | 实际状态：`new`/`pending`/`assigned`/`accepted`/`preparing`/`starting`/`running`/`complete`/`failed`/`shutdown`/`rejected`/`remove`/`orphaned` |
| `Status.Message` | 人类可读原因（如 `shutdown`、`No suitable node`） |
| `Status.ContainerStatus` | `{ContainerID, ExitCode, PID}` |
| `Status.Timestamp` | 状态变化时间 |

## Swarm Node

| 字段 | 说明 |
|---|---|
| `ID` | 25 字符节点 id |
| `Spec.Role` | `manager` / `worker` |
| `Spec.Availability` | `active` / `pause` / `drain` |
| `Spec.Labels` | 节点标签（placement 用 `node.labels.x == y`） |
| `Description.Hostname` | 主机名 |
| `Description.Resources.NanoCPUs` | CPU（**÷1e9 = 核数**） |
| `Description.Resources.MemoryBytes` | 内存字节 |
| `Description.Engine.EngineVersion` | Docker 版本 |
| `Description.Platform` | `{Architecture, OS}` |
| `Status.State` | `ready` / `down` / `unknown` |
| `Status.Addr` | 节点地址 |
| `ManagerStatus.Reachability` | `leader` / `reachable` / `unreachable` |

## Portainer Endpoint

| 字段 | 说明 |
|---|---|
| `Id` | 环境 id（**所有 docker 透传都用它**） |
| `Name` | 名称 |
| `Type` | **1=docker，2=docker-swarm**，3=azure，4=edge-agent，5=kubernetes |
| `URL` | Agent 地址（如 `tcp://tasks.agent:9001`） |
| `Status` | **1=up，2=down** |
| `Snapshots[0]` | 汇总：`StackCount/ServiceCount/ContainerCount/ImageCount/VolumeCount/NodeCount` |
| `Agent.Version` | Agent 版本 |
| `ComposeSyntaxMaxVersion` | 允许的最高 compose 版本（本环境 `3.9`） |

## Portainer User

| 字段 | 说明 |
|---|---|
| `Id` | 用户 id |
| `Username` | 用户名 |
| `Role` | **1=admin，2=user** |
| `EndpointAuthorizations` | 端点级授权列表 |
| `TeamMemberships` | 所属团队 |
| `LastCheckInDate` | 上次活跃 |

## 枚举速查

```yaml
Stack.Type:        { 1: swarm, 2: compose(standalone), 3: kubernetes }
Stack.Status:      { 1: active, 2: inactive }
Endpoint.Type:     { 1: docker, 2: docker-swarm, 3: azure, 4: edge-agent, 5: kubernetes }
Endpoint.Status:   { 1: up, 2: down }
User.Role:         { 1: admin, 2: user }
Service.Mode:      { Replicated: {Replicas: N}, Global: {} }
Registry.Type:     { 1: dockerhub, 2: ghcr, 3: custom, 4: quay, 5: azure, 6: gitlab, 7: ecr, 8: proget }
Node.Availability: { active, pause, drain }
Task.State:        { new, pending, assigned, accepted, preparing, starting,
                     running, complete, failed, shutdown, rejected, remove, orphaned }
```

## CLI 对「副本数」的口径

CLI 表格里的 `运行/期望` 列：

- replicated：`<实际 running 任务数>/<Spec.Mode.Replicated.Replicas>`
- global：`<实际 running 任务数>/global`（global 没有固定目标数，等于节点数）

**`docker service ls` 的 REPLICAS 列不可靠**（历史上出现过显示 1/1 但实际没运行的情况），
以任务统计为准；Portainer 快照里的 `RunningContainerCount` 也偏滞后。