# 实测坑全集（Portainer CE 2.39.4 + Docker 29.6.1 + 单节点 Swarm）

> 标注：**[实测]** 在 `192.168.0.57:9000` 跑过；**[spec]** 来自官方 OpenAPI；**[未验证]** 环境不具备。
> 排障前先读本文件，尤其是前三条。

## 0. 🔴 操作纪律：不要拿真实资源 id 做「方法探测」

**事故**：对真实服务 id 发 `DELETE` 只为「看看代理支持哪些方法」，服务被真删（32 → 31）。

| 禁止 | 正确做法 |
|---|---|
| 对真实 id 发 `DELETE`/`PUT`/`POST` 探测 | 只读探测只用 `GET`；写操作先有**明确意图**并经人类确认 |
| 认为「404 就说明安全」 | 404 只说明**该 id 不存在**；真实 id 上 `DELETE` 会真执行 |
| 在生产 endpoint 上试 | 先在测试环境验证；或只跑 `--dry-run` |

**误删后的恢复路径**（已验证可行）：从服务快照取完整 `Spec` → `docker service create` 按原样重建 →
补齐**容器级** `com.docker.stack.namespace` 标签（见第 5 条）。bind 挂载与外部数据库的数据不受服务删除影响。

## 1. 【实测】Docker 透传层只放行 GET 与 DELETE，不放行 PUT/POST

```
GET    /api/endpoints/1/docker/services/{id}  → 200
PUT    /api/endpoints/1/docker/services/{id}  → 404 page not found   ← 代理层无路由
POST   /api/endpoints/1/docker/services/{id}  → 404 page not found
PATCH  /api/endpoints/1/docker/services/{id}  → 404 page not found
DELETE /api/endpoints/1/docker/services/{id}  → 200（真实删除！）
```

**后果**：服务级扩缩容/改镜像**没法**用 `PUT /docker/services/{id}`。必须走：
`PUT /api/stacks/{id}?endpointId=N`（改 compose 重新部署）或 SSH `docker service update`。

**判别**：`404 page not found` 是裸文本 = 代理无路由；`{"message":"..."}` = 引擎真实响应。

## 2. 【实测】JWT 放 `X-API-KEY` 会 401

```bash
curl -H "X-API-KEY: $JWT"        .../api/stacks   # 401 {"message":"Invalid JWT token"}
curl -H "Authorization: Bearer $JWT" .../api/stacks   # 200
```

官方 spec 的 `securitySchemes` 明确区分：`ApiKeyAuth` 用 `X-API-KEY`（API key），
`jwt` 用 `Authorization`。**两者不能混用。**

## 3. 【实测】登录失败有三种响应，必须分清

| 场景 | HTTP | 响应体 | 处理 |
|---|---|---|---|
| 账号密码错误 | **422** | `{"message":"Invalid credentials","details":"Unauthorized"}` | 检查凭据 |
| **连续登录触发限流** | **403** | `{"message":"Access denied","details":"Access denied to resource"}` | **等几分钟**，或改用已有 JWT 走 `--token` |
| JWT 失效 | 401 | `{"message":"Invalid JWT token"}` | 重新登录 |

**踩坑经过**：跑完整回归时连续调了 30+ 个命令，每个子进程都重新登录一次，触发了 403。
此时**旧 JWT 仍然有效**（同一 JWT 打 `/api/stacks` 依旧 200）——所以 403 不代表凭据坏了。

**规避**：
- 批量脚本里复用同一个 JWT：先登录一次，`export PORTAINER_TOKEN=$JWT`，后续命令都走 `--token`
- CLI 的 JWT 缓存只在**单进程内**有效，跨进程不共享，所以「每个命令一个进程」的批量场景会重复登录

## 4. 【实测】Portainer 运行时不自带 OpenAPI

```
GET /api/openapi.json    → 404 page not found
GET /api/docs            → 404 page not found
GET /api/swagger.json    → 404 page not found
```

规格只能从 `https://api-docs.portainer.io/versions/ce/<version>/openapi.yaml` 取，
且它是**索引文件**（每条 path 用 `$ref` 指向同目录 `<tag>.yaml`），要拿完整定义得再下对应 tag 文件。

## 5. 【实测】`docker service create` 重建服务时，容器级 stack 标签会丢

只给 `--label com.docker.stack.namespace=x` 设的是**服务级**标签；容器还需要
`--container-label`（或事后 `docker service update --container-label-add`）才有。

**影响**：容器列表里 `Labels["com.docker.stack.namespace"]` 为 null ⇒
按栈过滤容器会漏掉该服务（`containers list --stack` 返回 0），`docker stack ps` 的展示也会异常。
服务本身能正常跑，所以容易忽略。

**判别**：
```bash
curl -s -H "Authorization: Bearer $JWT" .../docker/containers/json \
  | jq -r '.[]|"\(.Names[0])\t\(.Labels["com.docker.stack.namespace"] // "null")"'
```

## 6. 【实测】容器日志带 Docker 流复用帧头

`GET /docker/containers/{id}/logs?stdout=1&stderr=1` 返回的文本前面混有 8 字节帧头
（`\x01\x00\x00\x00\x00\x00\x00\x00` 之类），直接 `print` 会出现前导乱码/缩进错位。

**处理**：CLI 原样输出（方便 `| grep`），需要干净文本时用 `--json` 或接 `tr -d '\000'`。
根治要请求头 `Upgrade: tcp` 走帧解析，CLI 未做（标准库不便）。

## 7. 【实测】`GET /api/system/info` 基本没数据

```json
{"Platform":null,"Version":null,"DockerVersion":null,"IsSwarm":null,"NodeName":null}
```

真实信息要打 `/api/endpoints/{id}/docker/info` 和 `/docker/version`。

## 8. 【实测】Endpoint 快照的 `DockerSnapshotRaw` 被裁剪

`GET /api/endpoints/1` 的 `Snapshots[0]` 有 `StackCount/ServiceCount/ContainerCount/ImageCount` 等汇总，
但 `DockerSnapshotRaw.Swarm` 是 **null**、`Heartbeat=false`、`ContainerEngine=""`。

- 要 Swarm 拓扑 → `GET /docker/swarm`
- 要实时容器数 → `GET /docker/info`（`Containers` / `ContainersRunning`）
- 快照的汇总是**滞后**的，别当实时值用

## 9. 【实测】栈名 ≠ 服务名，三种写法都要认

```
栈名            n8n
服务全名        n8n_n8n          ← Spec.Name
服务简称        n8n              ← 去栈名前缀
```

- `services get n8n` 与 `services get n8n_n8n` 都能命中（CLI 三级匹配）
- 简称可能跨栈重名 → CLI 报错并列出全名，用 `栈:服务`（如 `a:n8n`）消歧
- 无栈服务（如 `sandbox-api`）的 `com.docker.stack.namespace` 为 null，简称规则不适用

## 10. 【实测】`PUT /api/stacks/{id}` 要**完整** StackFileContent，不是 patch

漏字段会被整段覆盖。正确姿势：先 `GET /stacks/{id}/file` 取原文，本地改，再整体提交。
`--dry-run` 打印的就是将要提交的完整内容。

## 11. 【实测】compose 缩进不统一，行级编辑必须探测缩进

本环境同一台机上见过 **2 空格**（`n8n` 栈）和 **4 空格** 两种服务块缩进。
硬编码缩进会导致「找不到服务」或「改错块」。

另外两个必踩的坑：
- 顶层 `networks:` / `volumes:` 也是「以冒号结尾的键」，不看缩进层级会被当成服务块，
  把 `replicas:` 插进去，生成 `networks:\n  replicas: 1` 这种坏文件
- 已改过 `replicas:` 后仍按「没找到就插入」逻辑走，会多插一行重复键

CLI 的 `_set_replicas` 用「先切服务块区间 → 区间内定位 → 有则改无则插」两步法解决，
对 4 空格缩进、多服务、global 模式（无 replicas）都做过边界用例。

## 12. 【spec】`PullImage` 已废弃，用 `RepullImageAndRedeploy`

```yaml
PullImage:
  description: Deprecated(2.36): use RepullImageAndRedeploy instead
RepullImageAndRedeploy:
  description: RepullImageAndRedeploy indicates whether to force repulling images and redeploying the stack
```

## 13. 【spec】整栈启停是独立端点，不是改副本数

```
POST /api/stacks/{id}/start?endpointId=N   StackStart
POST /api/stacks/{id}/stop?endpointId=N    StackStop
```

无请求体，`endpointId` 必填。探测它们存在可以用 `GET` 得到 **405**（路由在、方法不对）。

## 14. 【实测】任务列表含大量历史，要按 DesiredState 过滤

150 条任务里 107 条是 `shutdown`（历史）。查当前故障要过滤：

```bash
curl ... /docker/tasks | jq -r '.[]|select(.DesiredState!="shutdown")|"\(.Status.State)\t\(.ID[0:12])"'
```

失败类状态：`failed` / `rejected` / `orphaned` / `remove`。

## 15. 【实测】镜像 `RepoTags` 可能为空，`Image` 字段常带摘要

- 悬空镜像 `RepoTags` 是 `[]`
- 服务镜像形如 `nginx:1.27@sha256:abc…`，表格里要 `split("@")[0]` 否则挤爆列宽
- `docker info` 的 `ImageCount` 是 **null**，镜像数用 `Images`

## 16. 【实测】`volumes` 返回 `{Volumes:[...]}`，不是裸数组

`services`/`nodes`/`tasks`/`networks`/`images` 都是裸数组，只有 `volumes` 是包裹形态。
无 `UsageData` 时卷大小和引用数都是 `-`（本环境的卷全都没有 UsageData）。

## 17. 【未验证】多节点行为

本环境只有 **1 个 manager 节点**。以下结论**不能**直接套到多节点集群：
节点漂移、`global` 服务的实际副本数、多 manager 的 leader 选举、跨节点 overlay 连通性、
`node.role == manager` 之外的其他 placement constraints。

## 18. 【未验证】git 栈与 API key

本环境所有栈 `GitConfig` 为 null；`X-API-KEY` 鉴权仅有 spec 声明、未跑通。
CLI 的 git 相关能力（`StackUpdateGit`/`StackGitRedeploy`）未实现，需要时用 `raw`。