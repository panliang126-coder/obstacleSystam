# 系统数据流设计

## 1. 模块目标

定义从数据产生到飞行策略和控制确认的完整路径，包括消息顺序、时钟与坐标处理、并发、背压、超时、失败流和确定性回放。所有字段以 [统一接口规范](03_INTERFACE_SPEC.md) 为准。

## 2. 端到端数据流

```text
 [Radar] [EO/IR] [Weather] [GNSS] [IMU] [Flight Controller]
     \       |       |        |      |          /
      +------v-------v--------v------v---------+
      | Driver Decode / Calibration / Validate |
      +-------------------+---------------------+
                          |
                     SensorMessage
                          |
             +------------v-------------+
             | Time Sync + Frame Convert |
             +------------+-------------+
                          |
                    Event Bus (raw)
            +-------------+--------------+
            |                            |
   +--------v---------+          +-------v--------+
   | Perception/Fusion|          | Weather Fusion |
   +--------+---------+          +-------+--------+
            | TargetMessage              | EnvironmentMessage
            +-------------+--------------+
                          |
                 +--------v--------+
                 |  Digital Twin   |
                 +--------+--------+
                          | TwinSnapshot(revision)
                 +--------v--------+
                 | Risk Assessment |
                 +--------+--------+
                          | RiskMessage
                 +--------v--------+
                 | Path Planning   |
                 +--------+--------+
                          | PathMessage
                 +--------v--------+
                 | Decision Center |
                 +--------+--------+
                          | DecisionMessage
                 +--------v--------+
                 |  Safety Gate    |
                 +--------+--------+
                          | ControlCommand
                 +--------v--------+
                 | Control Gateway |
                 +--------+--------+
                          | Ack / VehicleState
                          +------------------> Event Bus

 Every validated business event --> Event Store
 Event Bus --> GUI / Monitoring / Historical Replay
```

## 3. 通用处理阶段

每条外部数据必须经过以下顺序：

1. **采集**：驱动读取字节/SDK 回调，附源序列号和源时间。
2. **解码**：转为内部 DTO；保留厂商原始类型只在适配器内部。
3. **校验**：范围、长度、CRC、枚举、必填字段和 Schema。
4. **标定**：应用设备标定版本，产生 `calibration_id`。
5. **时间同步**：估计源时钟偏移/不确定度，形成统一 UTC 时间。
6. **坐标转换**：转换到 WGS-84/ENU，附 `frame_id` 和 transform 版本。
7. **发布**：封装统一 Envelope，发布到数据面 Topic。
8. **消费与派生**：模块产生新事件，继承 `trace_id`，用 `causation_id` 指向直接原因。
9. **持久化**：重要事件写入事件存储；高频原始数据按策略降采样或外置对象存储。
10. **观测**：记录消息年龄、处理耗时、队列深度、丢弃原因和质量。

## 4. 时间模型

### 4.1 三种时间

| 字段 | 含义 | 用途 |
|---|---|---|
| `observed_at` | 设备认为数据发生的 UTC 时间 | 传感器融合、回放 |
| `received_at` | 适配器收到数据的 UTC 时间 | 延迟监测、故障判断 |
| `monotonic_ns` | 当前节点单调时钟值 | 本机超时和耗时，不跨主机比较 |

消息的新鲜度为 `now_utc - observed_at`，但需考虑 `clock_uncertainty_ms`。只有 `observed_at` 位于融合水位线内的数据才进入同一融合窗口。

### 4.2 融合水位线

- 默认容忍乱序：雷达 100 ms、视频 80 ms、GNSS/IMU 30 ms、天气 2 s。
- 水位线 = 当前最新源时间 − 该源乱序容忍度。
- 晚于窗口的数据正常处理；迟到数据记录为 `late`，只能修正历史/统计，不能反向改变已下发控制决策。
- 任何阈值必须可配置并写入运行快照。

## 5. 坐标转换流

```text
device frame
   -> calibrated sensor frame
   -> vehicle body frame (FRD adapter boundary)
   -> local navigation frame (ENU domain standard)
   -> WGS-84 when persistence/external exchange requires
```

- 领域标准局部坐标是 ENU；若飞控使用 NED，转换仅在飞控适配器内完成。
- transform 必须按时间查询，不能用“当前姿态”转换旧数据。
- 找不到有效 transform 时，消息标记 `transform_unavailable` 并隔离，不能假设零位姿。

## 6. 核心事件顺序

### 6.1 感知与 Twin 更新

```text
SensorDriver
  -> sensor.raw.<type>
  -> TimeSynchronizer
  -> sensor.normalized.<type>
  -> PerceptionPlugin(s)
  -> perception.targets
  -> TrackFusion
  -> perception.tracks
  -> TwinUpdater
  -> twin.snapshot
```

Track Fusion 以 `(source_id, source_track_id)` 建立关联，生成系统级 `track_id`。Track 删除使用 tombstone/`LOST` 状态，避免消费者把静默消失解释为安全。

### 6.2 天气流

```text
WeatherSensor/ExternalAdapter
  -> sensor.normalized.weather
  -> WeatherQC
  -> WeatherFusion
  -> environment.update
  -> TwinUpdater
  -> twin.snapshot
```

天气数据必须携带空间覆盖和有效时间；单点观测不能默认代表整个航线。

### 6.3 风险、规划、决策

```text
twin.snapshot(revision=N)
  -> RiskEngine -> risk.update(twin_revision=N)
  -> Planner -> path.proposed(twin_revision=N, risk_id=R)
  -> DecisionCenter -> decision.proposed(path_id=P, risk_id=R)
  -> SafetyGate -> decision.authorized / decision.rejected
  -> ControlGateway -> control.command
  -> FlightController -> control.ack + vehicle.state
```

若 Twin 已推进超过允许 revision/时间，Safety Gate 必须要求重新评估或重新规划。

## 7. Topic 与消费者

Topic 的正式名称见 `03_INTERFACE_SPEC.md`。主要消费关系：

| Topic | 生产者 | 消费者 | 处理语义 |
|---|---|---|---|
| `sensor.normalized.*` | Driver/Normalizer | Perception、Weather、Recorder | 高频、可按源丢旧 |
| `perception.targets` | Perception | Fusion、Twin、Recorder | 按批次有序 |
| `environment.update` | Weather | Twin、Risk、GUI | 保留最新+历史 |
| `twin.snapshot` | Twin | Risk、Planning、GUI | revision 单调 |
| `risk.update` | Risk | Planning、Decision、GUI | 至少一次、幂等 |
| `path.proposed` | Planning | Decision、GUI、Recorder | 至少一次 |
| `decision.proposed` | Decision | Safety Gate、GUI、Audit | 不丢 |
| `decision.authorized` | Safety Gate | Control Gateway、Audit | 不丢、严格权限 |
| `health.update` | 所有服务 | Management、Risk、GUI | 保留最新 |

## 8. 背压与队列策略

| 数据类型 | 队列 | 满时策略 | 原因 |
|---|---:|---|---|
| 视频帧 | 2–3 帧/相机 | 丢最旧，保留最新 | 避免累积视觉延迟 |
| 雷达扫描 | 2 个完整扫描 | 丢最旧完整扫描，不拆扫描 | 保持扫描一致性 |
| IMU | 200–500 样本 | 按窗口聚合；超限计数 | 高频、窗口化使用 |
| 天气 | 10 个更新 | 合并同区域旧更新 | 变化较慢 |
| Risk/Path/Decision | 有界持久队列 | 禁止静默丢弃；超时报警/降级 | 安全关键 |
| 日志/指标 | 批量缓冲 | 采样非关键日志 | 不反压数据面 |

每个消费者必须声明最大处理时间、队列长度和超限行为，禁止无界队列。

## 9. 超时与过期

默认基线：

| 数据 | 过期阈值 | 过期行为 |
|---|---:|---|
| IMU/姿态 | 100 ms | 禁止生成新控制策略 |
| GNSS/定位 | 500 ms | 切换惯导/估计并升高定位风险 |
| 动态目标航迹 | 1 s | 外推并扩大不确定度；随后转 LOST |
| 局部路径 | 500 ms 或约束变化 | 触发重规划 |
| 风险结果 | 500 ms | 不得授权基于旧风险的新路径 |
| 近场天气 | 5 s（设备可调整） | 使用保守边界 |
| 远场天气 | 60 s | 标记低置信度并限制任务 |
| 控制 Ack | 200 ms | 重试至上限后进入通信失效策略 |

超时基于注入式 Clock，测试不得调用不可控的系统时间。

## 10. 去重、顺序和幂等

- 所有事件使用 UUIDv7 `event_id`。
- 同一源 `sequence` 单调递增；重启时 `source_session_id` 变化。
- 消费者缓存近期 `event_id`，持久消费者在数据库保存 offset/idempotency key。
- 同一 `aggregate_id`（例如 `track_id`、`mission_id`）用 `revision` 检测旧写。
- 控制命令必须有 `idempotency_key`；重复命令返回相同业务结果，不重复产生副作用。
- 跨 Topic 不假设总顺序，使用 `causation_id` 和 revision 建立因果。

## 11. 失败数据流

### 11.1 非法消息

```text
invalid message -> quarantine topic -> structured error -> metric + sampled payload
```

安全关键 Topic Schema 非法时不得继续；原始 payload 只在访问受控的隔离存储保留，日志中脱敏。

### 11.2 插件失败

```text
plugin timeout/crash
  -> cancel current invocation
  -> health.update(DEGRADED)
  -> retry only if idempotent
  -> activate validated fallback
  -> if no fallback: risk escalates + safe policy
```

### 11.3 数据源冲突

多源结论冲突时保留各自观测和不确定度，不通过简单平均掩盖冲突。Fusion 输出 `conflict=true`，Risk Engine 将冲突作为风险因子。

## 12. 确定性回放

回放包必须包含：

- 事件日志或原始数据引用；
- Scenario ID、`run_id`、随机种子；
- 配置快照及哈希；
- Schema、插件、模型和标定版本；
- 地图和天气数据版本；
- 原始事件顺序与时间。

回放规则：

1. 使用 `ReplayClock`，不读取墙钟参与业务判断。
2. 所有随机算法从 `RandomProvider` 获取带名称的派生种子。
3. 禁止回放向 LIVE 控制适配器发布。
4. 可选择实时、倍速、逐事件和断点模式。
5. 输出事件单独写入新 `run_id`，不得污染原记录。

## 13. 数据流观测指标

- `events_received_total{topic,source}`
- `events_rejected_total{reason}`
- `event_age_ms{topic}`
- `processing_latency_ms{stage}`
- `queue_depth{consumer}`
- `dropped_events_total{policy}`
- `clock_offset_ms{source}`
- `clock_uncertainty_ms{source}`
- `trace_completion_total{outcome}`

风险、路径和决策事件的 trace 保留率必须为 100%。

## 14. 外部依赖

- Event Bus 端口与具体 MQTT/NATS/ROS2 适配器。
- Clock、FrameTransform、Schema Registry、Repository 端口。
- 时空索引、地图、模型运行时和控制网关。

任何依赖不可用时的行为必须显式配置并由故障注入测试覆盖。

## 15. 验收标准

1. 一条模拟雷达数据可追踪至 Decision 和 Control Ack。
2. 每个派生事件保留相同 `trace_id` 并正确设置 `causation_id`。
3. 乱序、重复和延迟消息不会产生重复控制副作用。
4. 视频/雷达消息风暴时队列有界，Risk/Decision Topic 不丢失。
5. 过期数据在规定阈值内触发降级。
6. 相同回放包运行两次，规范化后的关键输出完全一致。
7. NED/ENU 和源时间不一致测试能被发现而不是静默融合。

## 16. 测试方法

- 构造每类消息的正常、边界、非法、重复、乱序和过期用例。
- 使用虚拟时钟执行融合水位线和超时测试。
- 注入 10%/30% 丢包、100–1000 ms 抖动、源时钟漂移和总线断连。
- 执行持续消息风暴，断言队列上限、丢弃策略和内存稳定。
- 用固定场景生成 golden trace，对 Risk/Path/Decision 做语义 diff。
