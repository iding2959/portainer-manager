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
---

# 多节点集群 + Portainer 2.45.1 补充实测（2026-11-19）

> 第二套环境：`http://192.168.48.62:9000`，Portainer **CE 2.45.1**、Docker **29.1.3**（API 1.52），
> **3 节点 Swarm**（全部 manager：`master` 48.61 / `node1` 48.62 / `node2`）。
> 这批结论补上了首版文档里标注为「未验证」的多节点部分。

## 19. 【实测】2.45.1 与 2.39.4 的核心 API 一致，CLI 同一套直接可用

`health` / `stacks` / `services` / `nodes` / `tasks` / `containers` / `networks` / `volumes` /
`images` / `endpoints` / `users` / `registries` / `raw` 全部 26 项命令在两版上回归通过。
`StackStart` / `StackStop` / `RepullImageAndRedeploy` 在 2.45.1 同样存在。

## 20. 【实测】创建栈必须带 `SwarmID`（两版都要求）

```json
POST /api/stacks/create/swarm/string?endpointId=1
{"Name":"...","StackFileContent":"...","SwarmID":"4marnwyvnrnj8nc3uvothnezi"}
```

漏传 → `400 {"message":"Invalid request payload","details":"Invalid Swarm ID"}`。
官方 spec 的 `stacks.swarmStackFromFileContentPayload.required` 里就有 `SwarmID`。

**取法**：Endpoints 快照的 `DockerSnapshotRaw.Swarm` 是 null，只能实时打
`GET /api/endpoints/{id}/docker/swarm` 拿 `.ID`。CLI 已自动取（`--swarm-id` 可覆盖）。

## 21. 【实测 · 高风险】PUT 返回 200 不代表栈文件真的改了 → 必须回读校验

在 2.45.1 上遇到过：`PUT /api/stacks/{id}` 返回 `200`，但随后 `GET /stacks/{id}/file`
拿到的还是旧内容。**而 `scale` 是以栈文件为准重新部署的**，于是「改了镜像 → 扩容 →
镜像被悄悄改回去」。现场就要多排查一轮。

**CLI 处置**：`stacks update` 与 `services scale` 写完都回读栈文件，内容不一致直接报错，
不再静默成功。

## 22. 【实测】`--set-image` 只改栈文件，不会同步已偏离的服务

`stacks update --set-image` 改的是 compose 文本并触发重新部署。如果实际运行的服务镜像
早已偏离栈文件（比如手工 `docker service update` 过），一次 `--set-image` 只会把栈文件
推到目标值。改完务必核对：

```bash
python3 portainer.py stacks file <stack> | grep image:
python3 portainer.py services list --stack <stack>
```

## 23. 【实测】跨节点容器日志取不到（多节点核心限制）

`GET /api/endpoints/{id}/docker/containers/{cid}/logs` **只能访问 agent 所在节点**的容器。
任务被调度到别的节点时，容器 id 在列表里查得到，取日志却返回：

```
404 {"message":"No such container: <id>"}
```

同理 `containers/json` 也只列 agent 本机的容器（3 节点集群里 endpoint 1 只看到 22 个，
而集群总容器 63 个）。

**处置**：SSH 到该容器所在节点执行 `docker logs <id>`；或直连该节点的 agent。
CLI 的 `services logs` 已把这条限制写进报错提示，并给出目标节点名。

## 24. 【实测】`StackStop` 会移除服务定义，`StackStart` 只重建「0 副本」态

| 操作 | 结果 |
|---|---|
| `POST /stacks/{id}/stop` | 服务被移除（`services list --stack` 变空） |
| `POST /stacks/{id}/start` 对 active 栈 | `409 {"message":"Unable to start stack","details":"Stack is already active"}` |
| `POST /stacks/{id}/start` 对已停栈 | 实测**超时**（ingress 拉服务期间不回包），但服务定义会重建为 `0/0` |
| `services scale -r N` | 稳定可用，是恢复副本的**推荐**方式 |

**结论**：整栈启停用于「整栈下线/上线」，日常停某个服务用 `services scale -r 0`；
`StackStart` 之后若副本是 0，仍需一次 `services scale` 才能拉起。

## 25. 【实测】镜像拉不到时任务是 `rejected` 不是 `failed`，且跨节点反复重试

把镜像改成不存在的 tag 后：

```
slot 1  desired=running  state=preparing   ← 仍在拉
slot 1  desired=shutdown state=rejected    ← 拉失败的历史任务
```

- 失败状态是 **`rejected`**（不是 `failed`），`--failed-only` 必须把它算进去
- 调度器会在**节点间反复重试**（master → node2 → …），所以 tasks 列表里同一 slot 有多个任务
- 服务一直保持 `0/N`，不会自动放弃

## 26. 【实测】多节点上 `node2` 的 `Status.Addr` 是 `0.0.0.0`

3 节点里 `node2` 的 `Status.Addr` 显示 `0.0.0.0`，另两个是真实 IP。
按地址做节点识别会失效，用 `Description.Hostname` 或节点 ID。
（`ManagerStatus.Reachability` 三个都是 `reachable`，没有区分出 leader —— 该字段在此环境不可靠。）

## 27. 【实测】扩缩容默认跨节点均匀分布

`replicas: 3` 在 3 节点集群上落到 3 个不同节点（spread 策略默认生效）：

```
槽位1 → master   槽位2 → node1   槽位3 → node2
```

## 28. 【实测】ingress 端口在任意节点都可访问

发布 `18080:80` 后，`http://192.168.48.62:18080` 与 `http://192.168.48.61:18080`
都返回 200（ingress 路由把请求转给实际持有副本的节点）。
缩容到 0 后无服务监听该端口，连接直接失败（curl exit 7）。

---

# 从 183 现场巡检验证的补充（来源：`ops/deploy/findings/portainer-iding-net.md`）

> 这些结论来自 183 单节点 Swarm（Portainer CE 2.45.3）的长期运维记录，与本 skill 互补。
> 只收录**技能此前未覆盖、且会真实影响操作**的条目。

## 29. 【实测】JWT 与 access token 是两种凭据，只有后者能用 `X-API-KEY`

容易混淆，必须分清：

| 凭据 | 获取方式 | 发送头 |
|---|---|---|
| **JWT** | `POST /api/auth {Username,Password}` | `Authorization: Bearer <jwt>` |
| **Access token** | Portainer UI → My account → Access tokens | `X-API-Key: <token>` |

把 JWT 放 `X-API-KEY` → `401 Invalid JWT token`（本 skill 第 2 条）。反过来 access token 如果当 Bearer 发也不会认。
**CLI 目前只支持 JWT 通道**（`--token` 走 Bearer）；要长期自动化，推荐在 UI 生成 access token 后
用 `raw` 或 curl 配 `X-API-Key`，别复用到 CLI 的 `--token`。

## 30. 【实测】旧建栈路径在 2.45.x 已 405

```
POST /api/stacks?type=swarm&method=string&endpointId=1     → 405（2.45.3）
POST /api/stacks/create/swarm/string?endpointId=1          → 200 ✅
```

2.45.x 只剩 `/api/stacks/create/{swarm,standalone}/{string,file,repository}` 这一套。
老教程/老脚本里的 `?type=&method=` 写法会直接 405。

## 31. 【实测】CLI 建的栈在 UI 里是 "Limited control"，且无法转正

- 表现：栈详情显示 `This stack was created outside of Portainer. Control over this stack is limited.`，
  Update / Remove 按钮灰掉。
- **不能用 API 转正**：用同内容再 `POST /api/stacks/create/swarm/string` 会 409
  `A stack with the normalized name 'xxx' already exists`；查 bbolt 库确认 DB 里根本没有该栈记录，
  409 来自「Swarm 里已存在同名栈」。
- 想变成 Total control 只能重建：`docker stack rm <name>` 后由 Portainer 重新创建。
- **对 Portainer 自己的栈是个死结**（删了没人发命令）：保持 CLI 管理即可，
  或把 Portainer 搬出 Swarm（宿主机 `docker run` + systemd），让它不再管理自己。

对 CLI 用户的含义：`stacks update` 走的是 Portainer API，**对 CLI 建的栈可能被拒**（Limited control）；
这类栈要改，用 `docker stack deploy` 重放 compose，别指望 API。

## 32. 【实测】`PublicURL` 为空会让端口链接变成 `http://:6185`

Stacks 页面里端口链接点不动/跳空白，根因是环境没填 Public IP：

```bash
curl -s $P/api/endpoints/1 | jq -r '.PublicURL'      # 空串
# 修复（最小 body 即可）
curl -X PUT $P/api/endpoints/1 -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' -d '{"PublicURL":"192.168.0.183"}'
```

修复后 href 从 `http://:6185` 变成 `http://192.168.0.183:6185`。
**只影响链接拼装，不影响服务本身**；数据库端口点开必然是空白（那不是网页）。

## 33. 【实测】`GET /api/system/status` 是免鉴权的，适合做 healthcheck

Portainer 自带这个端点不需要 token，返回 `{"Version":"2.45.3",...}`。
`portainer-ce` 与 `agent` 镜像都是 distroless（没有 sh/curl/wget/nc），
所以容器内做 healthcheck 要**从宿主挂一个静态 musl busybox**：

```yaml
volumes: [/home/iding/sh/bin/busybox:/busybox:ro]
healthcheck:
  test: ["CMD", "/busybox", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:9000/api/system/status"]
```

注意必须是 **musl 版**（`busybox:1.37-musl`）——默认 `busybox:1.37` 是 glibc 动态链接，
进 distroless 容器报缺 `ld-linux`。

## 34. 【实测】「页面偶尔很卡」通常是入口链路，不是 Portainer

定位顺序（同一时刻实测 183）：后端接口全在 1 ms 级，最重的 `containers/json?all=1` 也才 75 ms，
`images/json` 103 ms，LAN 下载 7 MB 的 `main.js` 11 ms。而走 Cloudflare 隧道的公网入口
TTFB 稳定在 **0.75–1.9 s**，且有 90 分钟 11 次的 QUIC 断线。

判据：**先测 `127.0.0.1:9000/api/system/status` 的 TTFB**。
如果它是毫秒级，问题就在反代/隧道/客户端，不在 Portainer 本身。
次要噪音：`SnapshotInterval=5m` 抓快照时接口会到百毫秒级；每 5 分钟一条 `403` 是版本检查在打
`api.github.com` / `downloads.portainer.io`，无影响。
