# 调用约定与端点速查（Portainer CE 2.39.4 实测）

> 基线：`http://192.168.0.57:9000`，Portainer **CE 2.39.4**，Docker **29.6.1**（API 1.55），单节点 Swarm `email-node`。
> 官方 spec：`https://api-docs.portainer.io/versions/ce/2.39.4/openapi.yaml`（索引，185 个 path；各 tag 的完整定义在同目录 `<tag>.yaml`）。

## 1. 鉴权

**JWT 必须走 `Authorization: Bearer <jwt>`。**

```bash
# 换 JWT（免鉴权端点）
curl -s -X POST http://192.168.0.57:9000/api/auth \
  -H 'Content-Type: application/json' \
  -d '{"Username":"admin","Password":"***"}' | jq -r .jwt

# 用 JWT
curl -s -H "Authorization: Bearer $JWT" http://192.168.0.57:9000/api/stacks
```

| 方式 | Header | 用途 | 实测 |
|---|---|---|---|
| JWT（账号密码换） | `Authorization: Bearer <jwt>` | 日常使用 | ✅ 200 |
| API key | `X-API-KEY: <key>` | 长期凭据 | 官方 spec 的 `ApiKeyAuth` |
| 把 JWT 放 `X-API-KEY` | ❌ | — | **401 `Invalid JWT token`** |

官方 spec 的 `securitySchemes`：

```yaml
ApiKeyAuth: { in: header, name: X-API-KEY, type: apiKey }   # API key 专用
jwt:        { in: header, name: Authorization, type: apiKey } # JWT 专用
```

JWT 默认 8 小时有效。CLI 进程内缓存，不落盘。

## 2. 两条通道

| 通道 | 前缀 | 能力 |
|---|---|---|
| **Portainer 管理面** | `/api/*` | 栈 CRUD、用户/团队、仓库、设置、系统状态 |
| **Docker API 透传** | `/api/endpoints/{id}/docker/*` | 服务/节点/任务/容器/网络/卷/镜像 **只读** |

### ⚠️ 透传层的方法白名单是不一致的

| 方法 | `/docker/services/{id}` | 说明 |
|---|---|---|
| `GET` | ✅ 转发 | 正常 |
| `DELETE` | ✅ **转发并真实执行** | 会真删服务 |
| `PUT` / `POST` / `PATCH` | ❌ `404 page not found` | 代理层没有路由 |

**判别法**：`404 page not found` 是**裸文本**（代理无此路由）；`{"message":"service xxx not found"}` 是 Docker 引擎的真实响应。

**结论：服务级更新/扩缩容不能走透传层**，只有三条路：
1. **栈更新**（`PUT /api/stacks/{id}?endpointId=N`）——改 compose 重新部署整栈，可审计、可回滚 ✅ 推荐
2. SSH 到节点 `docker service update`——精确改单服务，不重排其他服务
3. Portainer Web UI 的 Stack Editor

## 3. 三类 404 与主要错误形态

| 场景 | HTTP | 响应体 |
|---|---|---|
| 路由不存在 | 404 | `404 page not found`（裸文本，**不是 JSON**） |
| Portainer 库里没有该对象 | 404 | `{"message":"Unable to find a stack ...","details":"Object not found inside the database (bucket=stacks, key=9999)"}` |
| id 参数非法 | 400 | `{"message":"Invalid stack identifier route variable","details":"Strconv.Atoi: parsing \"abc\": invalid syntax"}` |
| 缺必填 query | 400 | `{"message":"Invalid query parameter: endpointId","details":"Missing query parameter"}` |
| 请求体不合法 | 400 | `{"message":"Invalid request payload","details":"Invalid stack file content"}` |
| Docker 层资源不存在 | 404 | `{"message":"service nosuchsvc not found"}` |
| 方法不允许 | 405 | 裸 `405` |
| 账号密码错误 | **422** | `{"message":"Invalid credentials","details":"Unauthorized"}`（**不是 401**） |
| JWT 失效/缺失 | 401 | `{"message":"Invalid JWT token","details":"Unauthorized"}` |
| 无权限 | 403 | `{"message":"Unauthorized"}`（Portainer 按用户/团队/端点授权） |

## 4. Stacks 端点（Portainer 管理面）

| 方法 | 路径 | operationId | 说明 |
|---|---|---|---|
| GET | `/api/stacks` | `StackList` | 栈列表 |
| GET | `/api/stacks/{id}` | `StackInspect` | 栈详情 |
| GET | `/api/stacks/{id}/file` | `StackFileInspect` | 栈 compose 文件 → `{"StackFileContent":"..."}` |
| PUT | `/api/stacks/{id}?endpointId=N` | `StackUpdate` | 更新栈（需完整 StackFileContent） |
| **POST** | `/api/stacks/{id}/start?endpointId=N` | **`StackStart`** | **启动整栈**（无 body） |
| **POST** | `/api/stacks/{id}/stop?endpointId=N` | **`StackStop`** | **停止整栈**（无 body） |
| POST | `/api/stacks/create/swarm/string?endpointId=N` | `StackCreateDockerSwarmString` | 从文本创建 Swarm 栈 |
| POST | `/api/stacks/create/swarm/file` | `StackCreateDockerSwarmFile` | 从上传文件创建 |
| POST | `/api/stacks/create/swarm/repository` | `StackCreateDockerSwarmRepository` | 从 Git 仓库创建 |
| DELETE | `/api/stacks/{id}?endpointId=N` | `StackDelete` | 删除栈 |
| PUT | `/api/stacks/{id}/associate` | `StackAssociate` | 关联资源控制 |
| POST | `/api/stacks/{id}/git` | `StackUpdateGit` | 改 Git 配置 |
| PUT | `/api/stacks/{id}/git/redeploy` | `StackGitRedeploy` | Git 栈重新部署 |
| POST | `/api/stacks/{id}/migrate` | `StackMigrate` | 迁移到其他端点 |

### StackUpdate 请求体（`stacks.updateSwarmStackPayload`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `StackFileContent` | string | **完整的** compose 内容（不是 patch） |
| `Env` | array | `[{name, value}]` 栈环境变量 |
| `Prune` | bool | 删除 compose 里已不存在的服务 |
| `RepullImageAndRedeploy` | bool | **2.36 起用它**：强制重新拉镜像再部署 |
| `PullImage` | bool | **已废弃**（2.36），别再用 |

## 5. Docker 透传端点（只读）

| 路径（前缀 `/api/endpoints/{id}/docker/`） | 返回 | 备注 |
|---|---|---|
| `_ping` | `OK` | 健康检查 |
| `version` | `{Version, ApiVersion, MinAPIVersion}` | **ApiVersion 在这里，`info` 里是 null** |
| `info` | 宿主信息 | `ImageCount`/`ApiVersion` 为 null，用 `Images` |
| `swarm` | Swarm 集群信息 | Endpoint 快照里的 `DockerSnapshotRaw.Swarm` 是 null，要打这里 |
| `nodes` | 裸数组 | `Description.Resources.NanoCPUs`（÷1e9=核数） |
| `services` | 裸数组 | 见下方字段位置 |
| `tasks` | 裸数组 | 150 条含历史；`DesiredState=shutdown` 的是旧任务 |
| `containers/json?all=1` | 裸数组 | 标签里带 swarm service id |
| `containers/{id}/logs?stdout=1&stderr=1&tail=N` | 文本 | **流复用帧头未剥离**，见 pitfalls 第 6 条 |
| `networks` | 裸数组 | |
| `volumes` | `{Volumes:[...]}` | **不是裸数组** |
| `images/json` | 裸数组 | `Size` 字节、`RepoTags` 可能空 |

### 字段位置（写代码照这个取）

| 数据 | JSON 路径 |
|---|---|
| 服务全名 | `service.Spec.Name`（形如 `n8n_n8n`） |
| 栈名 | `service.Spec.Labels["com.docker.stack.namespace"]`（无栈服务为 null） |
| 副本数 | `service.Spec.Mode.Replicated.Replicas`；global 模式是 `Mode.Global` |
| 镜像 | `service.Spec.TaskTemplate.ContainerSpec.Image`（常带 `@sha256:`） |
| 服务版本 | `service.Version.Index` |
| 端口 | `service.Endpoint.Ports[]`（`PublishedPort`/`TargetPort`） |
| 最近更新状态 | `service.UpdateStatus.{State,Message}` |
| 节点资源 | `node.Description.Resources.{NanoCPUs,MemoryBytes}` |
| 节点角色 | `node.Spec.Role`；leader 看 `node.ManagerStatus.Reachability == "leader"` |
| 任务状态 | `task.Status.State` / `task.DesiredState` / `task.ServiceID` / `task.NodeID` / `task.Slot` |
| 容器所属服务 | `container.Labels["com.docker.swarm.service.id"]` / `["com.docker.swarm.service.name"]` |
| 容器所属栈 | `container.Labels["com.docker.stack.namespace"]` |

## 6. Portainer 其他常用端点

| 路径 | 说明 |
|---|---|
| `GET /api/system/status` | 版本 + InstanceID（**免鉴权**） |
| `GET /api/system/info` | ⚠️ 字段多为 null（`Platform`/`Version`/`IsSwarm`），别用 |
| `GET /api/endpoints` | 环境列表，含 `Snapshots[0]` 汇总 |
| `GET /api/endpoints/{id}` | 单环境详情 |
| `GET /api/endpoints/{id}/settings` | 环境级设置 |
| `GET /api/users` | 用户（`Role`：1=admin 2=user） |
| `GET /api/teams` | 团队 |
| `GET /api/registries` | 镜像仓库（含凭据，**勿外传**） |
| `GET /api/settings/public` | 公开设置 |

## 7. 实测环境概况

```
Portainer  2.39.4  (InstanceID 03098a68-…)
Docker     29.6.1  API 1.55  min 1.40
宿主       Ubuntu 24.04.2 LTS  x86_64  48 核 / 135GB  hostname email-node  192.168.0.131
Swarm      单节点 manager  ID r0e14x2c…  名称 default
Endpoint   Id=1  Name=primary  Type=2(docker-swarm)  URL=tcp://tasks.agent:9001  Status=1(up)
规模       32 服务 / 27 栈记录 / 169 容器（55 运行）/ 545 镜像 / 90 卷 / 13 网络
网络       sf-net(overlay,attachable) / ingress / portainer_agent_network
```

**栈与服务数不相等是正常的**：`portainer_portainer`、`portainer_agent` 由 Portainer 自己承载，
没有对应的 stack 记录（CE 无法纳管承载自己的栈）。

## 8. 未在本环境验证的部分

- 多节点 Swarm（本环境只有 1 个 manager 节点）：节点漂移、global 服务分布、多 manager 选举均未验证
- Kubernetes 端点（本环境无 k8s 环境）
- Edge / Edge Stack / Edge Job（未启用）
- Git 栈（本环境所有栈 `GitConfig` 为 null）
- API key 鉴权（`X-API-KEY`）未实测，仅有官方 spec 声明