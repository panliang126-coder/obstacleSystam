# 系统管理模块

## 1. 模块目标

集中管理配置、日志、模型、插件、运行实例和健康状态，为开发、部署和现场运行提供版本化、可审计、可回滚的管理能力，同时不侵入安全关键数据面。

## 2. 模块职责

- 配置草稿、校验、审批、发布、作用域和回滚。
- 插件/模型发现、验证、兼容性、影子部署、激活和退役。
- 服务、驱动、队列、数据新鲜度和资源健康聚合。
- 结构化日志、指标、追踪和告警路由。
- Run/Scenario 生命周期与 manifest 管理。
- 用户/角色/权限和管理操作审计。
- 提供 GUI/CLI 使用的 Query/Command API。

不负责更改业务消息语义、替代 Safety Gate 或直接操作设备。

## 3. 输入

- `health.update`、Metrics、Trace、结构化日志。
- 管理命令：配置、插件、模型、run、日志级别、回放。
- Artifact 上传和 manifest。
- 服务 discovery/readiness。
- 用户身份、角色、审批和变更理由。

所有有副作用命令必须带 `idempotency_key`、actor、期望 revision 和审计原因。

## 4. 输出

- `config.changed`、`plugin.changed`、`model.changed`。
- `management.command.result`。
- 聚合 `system.health` 和告警。
- Run manifest/config snapshot/artifact manifest。
- Audit Record 和查询 API。

## 5. 内部结构与配置管理

### 5.1 作用域与优先级

```text
system default
  < deployment/site
  < vehicle type
  < vehicle
  < mission
  < run override (SIM/REPLAY only by default)
```

合并后生成不可变 `ConfigSnapshot` 和 SHA-256。每个服务启动/热加载时记录使用的 snapshot。

### 5.2 变更流程

```text
DRAFT -> SCHEMA_VALIDATED -> REVIEWED -> APPROVED
      -> SCHEDULED -> ACTIVE -> RETIRED
                         |
                         +-> ROLLED_BACK
```

- Schema、单位、引用和跨字段约束必须在批准前验证。
- 安全阈值、LIVE endpoint、Control policy 需要更高角色或双人审批。
- 热加载只允许 manifest 标记为 `hot_reload_safe` 的字段。
- 激活失败时所有实例回滚到上一有效 snapshot，不能部分静默成功。

## 6. 插件与模型管理

### 6.1 制品门禁

1. artifact URI/大小/哈希；
2. 签名/来源；
3. manifest Schema；
4. API 与 input/output Schema 兼容；
5. 依赖和硬件能力；
6. 安全扫描/许可证清单；
7. 单元、契约、性能、场景报告；
8. 批准状态。

### 6.2 部署流程

```text
REGISTERED -> VALIDATED -> STAGED -> WARMED
           -> SHADOW -> ACTIVE -> DRAINING -> RETIRED
                         |
                         +-> ROLLBACK
```

影子插件无控制副作用。切换记录旧/新版本、指标、actor、run 和回滚窗口。

## 7. 健康模型

组件状态：

- `HEALTHY`：依赖、数据新鲜度和 SLA 正常；
- `DEGRADED`：仍提供受限能力；
- `UNHEALTHY`：不能提供能力；
- `UNKNOWN`：无可靠心跳，不能解释为 HEALTHY。

Health payload 至少含 `component_id, capability, status, since, heartbeat_at, checks[], data_freshness, dependencies, suggested_action`。

系统健康不是简单多数票。关键能力（定位、风险、安全门控、控制链路）不健康时，系统等级必须按依赖图提升严重度并通知 Risk/Decision。

## 8. 可观测性

### 8.1 日志

结构化字段：

```json
{
  "timestamp": "...",
  "level": "INFO",
  "service": "risk-service",
  "instance_id": "edge-01",
  "run_id": "...",
  "trace_id": "...",
  "vehicle_id": "uav-001",
  "event": "risk_evaluated",
  "details": {}
}
```

- 禁止字符串拼接敏感 payload。
- ERROR 必须有稳定 `error_code`、retryable、异常链和上下文。
- 可动态调日志级别，但有期限并审计；禁止生产长期 DEBUG。

### 8.2 告警

告警含 severity、dedup key、首次/最近时间、run、证据、runbook 和确认状态。告警去重不删除事件历史。

## 9. 权限

建议角色：

| 角色 | 能力 |
|---|---|
| Viewer | 读状态、日志、历史 |
| Operator | 任务操作、告警确认，受 Safety Gate 限制 |
| Engineer | SIM/HIL 配置、插件影子、诊断 |
| Safety Approver | 安全阈值/LIVE policy 审批 |
| Admin | 身份、部署和基础设施；不能单人绕过安全审批 |

服务身份使用独立凭据和 Topic ACL。UI 角色不直接映射数据库超级权限。

## 10. 管理接口与 API

示例：

- `GET /v1/system/health`
- `GET /v1/runs/{run_id}`
- `POST /v1/runs`（SIM/REPLAY）
- `POST /v1/configs/{namespace}/drafts`
- `POST /v1/configs/{id}:validate|approve|activate|rollback`
- `POST /v1/plugins:register`
- `POST /v1/plugins/{name}/{version}:shadow|activate|rollback`
- `GET /v1/audit?from=&to=&actor=&resource=`

Command 返回 `202 Accepted` 和 operation ID；客户端查询 operation 状态，避免长连接超时后重复副作用。

## 11. Run Manifest

每次运行固定：

- run/scenario/mode/seed/时间；
- 代码 commit/build ID；
- 配置 snapshot/hash；
- Schema registry 版本；
- 插件、模型、驱动及 artifact hash；
- 地图、标定、天气数据版本；
- 主机/容器/CPU/GPU/加速运行时；
- 操作员/授权与 LIVE endpoint；
- 测试/审计链接。

缺少 manifest 的运行不能作为验收或安全比较证据。

## 12. 错误与降级

| 情况 | 行为 |
|---|---|
| Management 服务宕机 | 数据面继续使用已激活快照；禁止新变更 |
| 配置部分实例失败 | 回滚整次发布或保持旧版本，状态显式 failed |
| Artifact 校验失败 | 隔离，禁止加载 |
| Health 心跳丢失 | 状态 UNKNOWN，再由依赖图升级风险 |
| 日志后端不可用 | 有界本地 spool；满时采样非关键日志，保留审计 |
| 审计不可用 | 高风险管理动作 fail-closed |

## 13. 外部依赖

Config/Artifact/Audit Repository、EventBus、身份提供方、日志/指标/追踪后端、Secret Store 和部署编排器。管理模块不保存明文 Secret，只保存引用。

## 14. 验收标准

1. 每次配置、插件和模型变更可追踪 actor、理由、before/after hash 和结果。
2. 不兼容/损坏 artifact 加载成功数为 0。
3. 安全配置的未授权或单人越权激活成功数为 0。
4. 关键组件失联后 ≤ 2 s 显示 UNKNOWN/UNHEALTHY 并传递给 Risk。
5. Management 服务中断不停止已运行的数据面，但禁止新管理副作用。
6. 插件激活失败可在 30 s 内自动回滚到已验证版本。
7. Run manifest 完整率 100%。

### 14.1 Phase 6 可执行基线

- 管理命令强制包含 actor、role、reason、client time、expected revision 和
  idempotency key；重复键同内容返回原结果，不同内容拒绝。
- 配置实现 Draft→Schema Validated→Approved→Active 状态，安全 namespace/
  阈值需要两个不同 Safety Approver；明文 Secret 字段拒绝，只允许 `ref:` 引用。
- 插件实现 Registered/Validated→Shadow→Active→Rollback 基线，并校验 artifact
  SHA-256、签名结果和接口兼容结果。
- Health 聚合对超时心跳显示 UNKNOWN；关键 Risk/Safety Gate/Control Gateway
  UNKNOWN 或 UNHEALTHY 时系统显示 UNHEALTHY。
- 告警按 dedup key 合并但保留计数，确认只记录 actor/time，不修改风险等级。
- 当前为内存 Repository 基线；持久数据库、外部身份、制品签名服务和跨实例事务
  仍属于后续部署集成。

## 15. 测试方法

- 配置 Schema、作用域覆盖、并发 revision、审批和回滚测试。
- artifact 篡改、错误签名、不兼容 API、缺依赖和资源不足测试。
- 插件 shadow/activate/drain/rollback 状态机测试。
- RBAC、跨车辆/项目访问和审计不可变测试。
- 心跳丢失、告警风暴、日志后端/数据库不可用故障注入。
- 运行清单复现：用 manifest 启动 REPLAY 并比较关键输出。
