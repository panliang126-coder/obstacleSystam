# 数据库存储设计

## 1. 模块目标

为实时状态、历史事件、模型、配置、审计和回放提供可替换的数据访问层。业务模块只依赖 Repository 端口，不感知 SQLite、PostgreSQL 或 InfluxDB 的具体 API。

## 2. 存储分工

| 数据类别 | 主存储 | 开发/单机 | 用途 |
|---|---|---|---|
| 当前 Twin/运行状态 | 内存 State Store + PostgreSQL 快照 | SQLite | 低延迟读、重启恢复 |
| 业务事件与审计 | PostgreSQL | SQLite | 可追溯、回放索引、事务 |
| 高频遥测/指标 | InfluxDB | SQLite/Parquet 文件 | 时间窗口、降采样、趋势 |
| 原始视频/点云/雷达帧 | 对象存储/文件存储 | 本地受控目录 | 大对象，不写关系表 blob |
| 模型与插件包 | 对象存储 + PostgreSQL 元数据 | 本地 artifact 目录 | 版本、哈希、签名、回滚 |
| 配置 | PostgreSQL + Git/配置包 | SQLite/YAML | 版本、作用域、审批 |

PostgreSQL 或 InfluxDB 不可用时，安全关键决策不能依赖“写库成功”才能运行，但必须进入有界本地 WAL/Outbox；缓冲耗尽时发布健康告警并按策略降级。

## 3. Repository 端口

```python
class EventRepository(Protocol):
    async def append(self, event: Envelope) -> AppendResult: ...
    async def read(self, query: EventQuery) -> AsyncIterator[Envelope]: ...

class TwinRepository(Protocol):
    async def get_snapshot(self, vehicle_id: str) -> TwinSnapshot: ...
    async def compare_and_set(self, expected_revision: int, update: TwinUpdate) -> TwinSnapshot: ...

class ConfigRepository(Protocol):
    async def get_effective(self, scope: ConfigScope, at: datetime) -> ConfigSnapshot: ...
    async def publish(self, draft: ConfigDraft, expected_revision: int) -> ConfigVersion: ...

class ArtifactRepository(Protocol):
    async def put(self, stream: AsyncIterator[bytes], metadata: ArtifactMetadata) -> ArtifactRef: ...
    async def verify(self, ref: ArtifactRef) -> VerificationResult: ...
```

Repository 必须接受事务/幂等语义，不向领域层返回 ORM 实体。

## 4. 核心关系表

以下类型以 PostgreSQL 为准；SQLite 适配器映射 UUID/JSONB/TIMESTAMPTZ 为 TEXT。

### 4.1 `runs`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `run_id` | UUID | PK |
| `mode` | VARCHAR(16) | `SIM/REPLAY/HIL/LIVE` |
| `scenario_id` | TEXT NULL | 场景/回放 ID |
| `random_seed` | BIGINT NULL | 仿真种子 |
| `started_at/ended_at` | TIMESTAMPTZ | 生命周期 |
| `config_hash` | TEXT | 有效配置哈希 |
| `artifact_manifest` | JSONB | 插件、模型、地图、标定版本 |
| `status` | VARCHAR(16) | `STARTING/RUNNING/STOPPED/FAILED` |

索引：`(started_at DESC)`、`(mode,started_at DESC)`。

### 4.2 `event_log`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `event_id` | UUID | PK，去重键 |
| `run_id` | UUID | FK `runs` |
| `trace_id` | UUID | NOT NULL |
| `causation_id` | UUID NULL | 因果链接 |
| `topic` | TEXT | NOT NULL |
| `schema_name` | TEXT | 例如 `risk` |
| `schema_major/minor` | SMALLINT | 版本 |
| `source_service/instance` | TEXT | 来源 |
| `vehicle_id` | TEXT NULL | 车辆 |
| `observed_at/received_at` | TIMESTAMPTZ | 时间 |
| `sequence` | BIGINT | 源序列 |
| `quality` | JSONB | 质量 |
| `payload` | JSONB | 业务载荷或对象引用 |
| `payload_hash` | TEXT | 内容校验 |
| `inserted_at` | TIMESTAMPTZ | 默认 now |

索引：

- `(run_id, observed_at, event_id)`：回放；
- `(trace_id, observed_at)`：链路追踪；
- `(topic, observed_at DESC)`：Topic 查询；
- `(vehicle_id, observed_at DESC)`：车辆查询；
- JSONB 仅为已验证的高频查询建表达式索引，避免全字段 GIN 膨胀。

按 `observed_at` 月/周分区，具体粒度由数据量压测决定。

### 4.3 领域投影表

| 表 | 主键 | 关键字段 | 主要索引 |
|---|---|---|---|
| `track_state` | `track_id` | state、class、position、velocity、covariance、revision、last_seen | `(vehicle_id,last_seen DESC)`、空间索引 |
| `environment_snapshot` | `environment_id` | coverage、valid_from/to、risk_factors、grid_ref | 时间范围、空间覆盖 |
| `twin_snapshot` | `(vehicle_id,revision)` | observed_at、map_version、state JSONB、state_hash | `(vehicle_id,revision DESC)` |
| `risk_assessment` | `risk_id` | subject、twin_revision、score、level、valid_until、explanations | `(subject_id,created_at DESC)`、`(level,valid_until)` |
| `planned_path` | `path_id` | mission_id、revision、waypoints、costs、status、valid_until | `(mission_id,created_at DESC)` |
| `decision_record` | `decision_id` | action、risk_id、path_id、policy、authorization、expires_at | `(mission_id,created_at DESC)`、`(action,created_at)` |
| `control_record` | `command_id` | decision_id、idempotency_key、state、ack、timestamps | UNIQUE `idempotency_key` |

这些表是事件的查询投影，不取代 `event_log` 的审计事实。投影可由事件重建。

### 4.4 配置与制品表

`config_version`：

- `config_id UUID PK`
- `namespace TEXT`
- `scope_type/scope_id TEXT`
- `revision BIGINT`
- `content JSONB`
- `content_hash TEXT`
- `status DRAFT/ACTIVE/RETIRED`
- `valid_from TIMESTAMPTZ`
- `created_by/approved_by TEXT`
- `created_at TIMESTAMPTZ`
- UNIQUE `(namespace,scope_type,scope_id,revision)`

`plugin_registry` / `model_registry`：

- 稳定名称、SemVer、kind/framework；
- artifact URI、SHA-256、签名状态；
- API/Schema 兼容范围；
- 资源需求、目标硬件；
- 测试报告 URI、批准状态、创建/退役时间。

### 4.5 健康与审计

`health_event`：组件、状态、故障码、依赖、新鲜度、详情、开始/结束时间。

`audit_log`：actor、action、resource、before_hash、after_hash、result、reason、trace_id、client_ip、created_at。

`audit_log` 仅追加；应用角色无 UPDATE/DELETE 权限。

## 5. InfluxDB 测量设计

避免高基数标签：

| Measurement | Tags | Fields | 时间 |
|---|---|---|---|
| `vehicle_telemetry` | vehicle_id、run_id、mode | position、velocity、attitude、battery | observed_at |
| `sensor_health` | sensor_id、type、instance | fps、drop_rate、temperature、age_ms | received_at |
| `pipeline_latency` | service、stage、plugin_version | p50/p95/p99、queue_depth | received_at |
| `weather_sample` | source_id、region_id | wind、rain、visibility、temp | observed_at |
| `risk_score` | vehicle_id、dimension、level | score | observed_at |

不要把 `event_id`、`trace_id`、`track_id` 作为长期 tag；需要追踪时写 field 或查 PostgreSQL。

## 6. 大对象布局

```text
artifacts/
├── raw/{run_id}/{sensor_id}/{date}/{event_id}.{ext}
├── replay/{run_id}/manifest.json
├── models/{name}/{version}/{sha256}/...
├── plugins/{name}/{version}/{sha256}/...
├── maps/{map_id}/{version}/...
└── weather-grids/{date}/{environment_id}.zarr
```

数据库存 URI、哈希、大小、媒体类型、加密和保留信息。写入流程为：临时上传 → 哈希验证 → 原子发布引用 → 事件提交；孤儿对象由延迟清理任务处理。

## 7. 事务与一致性

- 业务状态更新与 Outbox 事件在同一 PostgreSQL 事务提交。
- Outbox 发布成功后标记，不直接在业务事务中等待外部 Broker。
- 消费端 Inbox/`event_id` 去重。
- Twin 使用 `expected_revision` 比较交换，冲突时重新读取，不覆盖新状态。
- 控制记录以 `idempotency_key` 唯一约束保证副作用幂等。
- 高频遥测可最终一致；Decision、Authorization、Control Ack 必须持久且因果完整。

## 8. 生命周期与保留

默认值必须可配置并受法规/磁盘预算约束：

| 数据 | 热存储 | 温存储 | 到期处理 |
|---|---:|---:|---|
| 原始高频传感器 | 7 天 | 30 天对象存储 | 删除或归档 |
| 规范化 Sensor 事件 | 30 天 | 180 天归档 | 降采样/删除 |
| Twin 快照 | 30 天全量 | 1 年关键帧 | 保留关键事件前后窗口 |
| Risk/Path/Decision/Control | 1 年 | 项目要求期限 | 默认不早于审计要求 |
| 指标 | 30 天 1s 粒度 | 1 年分钟粒度 | 聚合 |
| 审计记录 | ≥ 2 年 | 归档 | 需批准才销毁 |
| 模型/插件 | 活跃+前两版本 | 退役后 1 年 | 保留回放所需版本 |

回放清单引用的数据受 legal hold/实验锁保护，不被普通保留任务删除。

## 9. 迁移策略

- 使用单向、编号数据库迁移；应用启动不自动执行破坏性生产迁移。
- Expand/Contract：先加新列/表并双写，再迁移消费者，最后退役旧字段。
- Schema major 迁移保留事件原始版本，读取层负责 upcast。
- 每次迁移需有前置容量检查、校验 SQL、回滚或前滚方案。

## 10. 备份与恢复

- PostgreSQL：每日全量/连续 WAL（生产），季度恢复演练。
- InfluxDB：按 bucket 备份；可从事件/原始数据重算的派生指标可降低恢复优先级。
- 对象存储：版本控制/不可变策略；跨故障域复制按部署级别决定。
- 配置、插件、模型注册表与 artifact 必须成套恢复。

基线目标：

- 元数据与决策审计 RPO ≤ 5 min，RTO ≤ 30 min。
- 原始遥测按现场存储能力定义，任何丢失必须形成缺口记录。

## 11. 安全

- 应用、迁移、只读分析、备份使用不同数据库角色。
- 传输 TLS，磁盘/对象存储加密；密钥不写配置表。
- 查询 API 强制车辆/项目范围；大范围导出需审计。
- 日志/事件中的凭据、个人信息和不必要图像元数据脱敏。
- 审计表和生产 artifact 使用不可变/追加策略。

## 12. 外部依赖

PostgreSQL、InfluxDB、对象存储、迁移工具和数据库驱动均封装在 adapters。SQLite 是开发/边缘降级实现，不承诺替代生产时序吞吐。

## 13. 验收标准

1. SQLite 与 PostgreSQL Repository Contract Test 结果一致。
2. 同一事件重复写入只保留一条，业务投影不重复更新。
3. Decision → Risk/Path → 输入事件可在 2 秒内完成链路查询。
4. Twin 并发 revision 冲突不会丢更新。
5. 典型峰值 2 倍负载下写入 P95 达到部署预算且无无界积压。
6. 数据保留任务不会删除被回放清单引用的数据。
7. 从备份恢复后配置哈希、artifact 哈希和审计链校验通过。

## 14. 测试方法

- Repository 契约、事务回滚、并发、幂等和分页测试。
- 使用真实 PostgreSQL/InfluxDB 容器执行集成测试，不只使用 mock。
- 生成高基数、长时间和峰值突发数据做容量/索引压测。
- 杀死 Broker/数据库，验证 Outbox、缓冲上限和恢复重放。
- 执行迁移前滚、兼容双写、回滚/恢复演练。
- 校验权限矩阵、审计不可变、备份加密和敏感字段脱敏。
