# 系统总体架构

## 1. 模块目标

定义可部署、可测试、可演进的系统结构，明确分层职责、进程边界、依赖方向、实时预算、安全边界和运行模式，保证模拟器与真实设备只在适配器层发生替换。

## 2. 架构驱动因素

| 驱动因素 | 架构响应 |
|---|---|
| 多源异步数据 | 统一事件信封、时间同步、坐标转换和质量标记 |
| 算法快速迭代 | 插件协议、能力清单、版本约束、影子运行 |
| 真实设备晚接入 | Hexagonal Architecture；Driver/Simulator 共享端口 |
| 动态障碍低延迟 | 边缘部署、进程内快速总线、局部规划独立时延预算 |
| 安全要求 | 控制网关单一出口、过期检测、状态机、最小风险动作 |
| 可复现 | 注入式 Clock/Random、事件日志、配置快照、确定性回放 |
| 多种部署 | 单机组合、边缘+中心、容器化服务均遵守同一契约 |

## 3. 分层架构

| 层 | 目标与职责 | 输入 | 输出 | 稳定接口 | 直接依赖 | 验收标准 |
|---|---|---|---|---|---|---|
| Sensor Layer | 采集、解码、校验、标定、健康监测 | 设备字节流/SDK | `SensorMessage`、Vehicle State | `SensorDriver`、`FlightControllerPort` | 厂商 SDK、串口/CAN/UDP/MAVLink/ROS2 | 真实与模拟驱动通过同一契约测试 |
| Simulation Layer | 生成设备同构数据、场景、故障与回放 | 场景、种子、轨迹 | `SensorMessage` 等 | `SensorDriver`、`ScenarioSource` | 仿真引擎、数据集 | 固定种子输出一致；可注入丢包/延迟/漂移 |
| Digital Twin Layer | 维护统一世界状态与短时预测 | 目标、环境、飞行器、地图 | Twin Snapshot/Prediction | `TwinRepository`、Twin Event | 地图、时空索引 | 快照原子一致；陈旧状态被标记 |
| AI Perception Layer | 检测、跟踪、分类、Embedding、融合 | 标准化传感器事件 | `TargetMessage` | `PerceptionPlugin` | 模型运行时 | 性能与精度达到 05 文档阈值 |
| Weather Layer | 融合天气观测，形成环境风险因子 | 气象传感器/外部服务 | `EnvironmentMessage` | `WeatherPlugin` | 栅格/插值库 | 输出含时空覆盖、置信度与新鲜度 |
| Risk Layer | 多维风险评分、等级与解释 | Twin、目标、天气、健康 | `RiskMessage` | `RiskPlugin` | 规则/模型 | 保守处理缺失数据；结果可解释 |
| Planning Layer | 全局路径、局部避障、动态重规划 | 任务、Twin、Risk、约束 | `PathMessage` | `PlannerPlugin` | 几何、优化库 | 路径无硬约束冲突，满足时延预算 |
| Decision Layer | 策略仲裁与安全状态机 | Risk、Path、任务、系统状态 | `DecisionMessage` | `DecisionPolicy` | 规则/策略模型 | 相同输入确定性输出；所有决策可追溯 |
| Control Layer | 最终安全门控、指令适配和确认 | Decision、Path、Vehicle State | 控制命令、Ack | `ControlGateway` | PX4/ArduPilot/模拟飞控 | 非授权模式绝不发真实命令 |
| UI Layer | 实时展示、回放、受控操作 | 业务事件、查询 API | 管理/任务命令 | Query API、Command API | PyQt6 | UI 不阻塞数据面；权限和审计完整 |

系统管理、存储、日志、指标、鉴权属于横切能力，不成为业务层之间的隐式耦合通道。

## 4. 逻辑组件与端口

```text
Adapters (outside)                    Domain/Application (inside)

Radar SDK -------- RadarDriver ------> SensorIngestPort
Camera SDK ------- CameraDriver -----> SensorIngestPort
Simulator -------- SimDriver --------> SensorIngestPort
Replay ----------- ReplayDriver -----> SensorIngestPort
                                          |
                                          v
                                      EventBusPort
                                          |
  Model Runtime <-- PluginPort <---- Application Services
  PostgreSQL ---- RepositoryPort <--- Domain State
  MQTT/NATS/ROS2 - EventBusAdapter <-- EventBusPort
  PX4/MAVLink ---- ControlAdapter <-- ControlGateway
```

领域层不能感知 MQTT、数据库驱动、厂商 SDK 或 GUI 类型。所有外部依赖通过 `ports/` 定义的协议反转进入。

## 5. 建议进程边界

### 5.1 最小开发部署

一个 Python 进程内运行 Event Bus、模拟器、业务模块和 SQLite；GUI 独立进程。用于单元测试、SIL 和开发演示。

### 5.2 边缘生产部署

| 进程/服务 | 内容 | 失败影响 | 恢复策略 |
|---|---|---|---|
| `ingest-service` | 驱动、校验、同步、坐标转换 | 对应传感器不可用 | 驱动重连，发布健康事件 |
| `perception-service` | 感知插件与融合 | 航迹降级/过期 | 重启插件、启用基线模型 |
| `world-service` | 天气、数字孪生、风险 | 规划输入受限 | 读最后有效快照并标记过期 |
| `autonomy-service` | 规划、决策、安全前置 | 无新策略 | Control Gateway 执行超时策略 |
| `control-gateway` | 唯一真实飞控出口 | 进入飞控原生 failsafe | 独立 watchdog，禁止自动恢复 LIVE 权限 |
| `management-service` | 配置、插件、日志、健康 | 管理功能受限 | 不影响当前安全策略 |
| `gui` | 可视化和操作 | 失去人机界面 | 数据面继续运行 |

组件可合并部署，但逻辑边界和端口不得删除。

## 6. 数据面、控制面与管理面

- **数据面**：传感器、目标、环境、Twin、风险、路径和决策事件。强调低延迟、有界队列、Schema 校验。
- **控制面**：任务下发、模式切换、决策确认、飞控命令和 Ack。要求鉴权、幂等键、超时、审计。
- **管理面**：配置、插件、模型、日志、健康和指标。不得通过配置接口绕过控制面授权。

三者使用独立 Topic 前缀和权限。数据面拥塞不能阻塞控制面；管理面故障不能取消已有安全约束。

## 7. Event Bus 架构

`EventBusPort` 提供：

```python
class EventBusPort(Protocol):
    async def publish(self, topic: str, message: Envelope) -> PublishAck: ...
    def subscribe(self, topic: str, handler: EventHandler, *, group: str) -> Subscription: ...
    async def request(self, topic: str, command: Envelope, timeout_s: float) -> Envelope: ...
```

适配器：

- `InProcessEventBus`：测试与单机 SIL，`asyncio` 有界队列。
- `MqttEventBus`：设备/边缘互联，适合状态与命令 Topic。
- `NatsEventBus` 或等价实现：服务间低延迟、持久化流。
- `Ros2EventBusAdapter`：ROS2 生态接入，不把 ROS 消息类型泄漏到领域层。

消息语义：

- 高频原始帧允许 `at-most-once`，拥塞时按传感器策略丢旧帧。
- 风险、路径、决策和管理命令至少 `at-least-once`，消费者按 `event_id`/`idempotency_key` 去重。
- 跨服务不承诺全局 exactly-once；使用 Outbox、幂等消费和审计实现业务一致性。

## 8. 插件架构

插件包至少包含：

```yaml
name: local_rrt_star
version: 1.2.0
kind: planner
entrypoint: low_altitude_plugins.rrt:Plugin
api_version: "1.x"
input_schema: path_request/1.0
output_schema: path/1.0
capabilities: [local, dynamic_obstacles]
resources:
  cpu_cores: 2
  gpu_memory_mb: 0
```

生命周期：`discover -> validate -> load -> initialize -> warmup -> shadow -> activate -> drain -> unload`。

- 插件不能在导入阶段打开设备、线程或网络连接。
- 激活必须原子切换；旧插件排空在途任务后卸载。
- 影子插件只能观察和记录，不能产生控制效果。
- 插件崩溃由宿主隔离，失败时回退到已验证版本或安全基线。

## 9. 时间、坐标和状态一致性

- 事件同时记录 `observed_at`（源时间）、`received_at`（接收 UTC）和可选 `monotonic_ns`。
- 时间同步组件估计 `clock_offset_ms` 和 `clock_uncertainty_ms`；超过阈值则降低质量或拒绝融合。
- 全局位置采用 WGS-84：纬经度 degree、高度 `alt_m_msl`。
- 局部运算采用右手 ENU（m），消息必须带 `frame_id` 和参考原点版本。
- Twin 更新以单调 `revision` 提交；Risk/Path/Decision 记录所使用的 Twin revision。
- 不允许把不同参考原点或未知时间基准的数据直接融合。

## 10. 实时与资源预算

| 阶段 | P95 预算 | 队列策略 |
|---|---:|---|
| 驱动解码+校验 | 10 ms | 丢弃损坏帧，记录计数 |
| 同步+坐标转换 | 15 ms | 以源为单位有界缓冲 |
| 感知/融合 | 80 ms | 视频优先保留最新帧；雷达保留扫描完整性 |
| Twin+风险 | 35 ms | 合并过时状态更新，不丢决策相关事件 |
| 局部规划 | 100 ms | 新请求取消旧的未提交请求 |
| 决策+安全前置 | 20 ms | 不丢；超时进入安全策略 |

总预算不是各阶段机械相加；感知与天气可并行。每个进程必须暴露队列深度、处理耗时、丢弃数和消息年龄。

## 11. 失效隔离与降级

| 故障 | 检测 | 降级行为 |
|---|---|---|
| 单传感器离线 | heartbeat/消息年龄 | 降低融合置信度，使用剩余源，发布健康告警 |
| 时间同步失效 | offset/uncertainty 超阈值 | 隔离该源，不参与时序融合 |
| 感知插件崩溃 | watchdog/异常 | 回退基线插件；无可用插件则风险升高 |
| 天气数据过期 | freshness | 使用保守环境边界，禁止穿越未知高风险区 |
| Event Bus 拥塞 | 队列和延迟 | 丢旧高频帧，保留控制/风险/决策 |
| 规划超时 | deadline | 复用仍安全的短期路径，否则 Hold/Return |
| 通信丢失 | link heartbeat | 按任务政策 Return/Land/Hold |
| Control Gateway 异常 | 独立 watchdog | 停止新命令，飞控原生 failsafe 接管 |

## 12. 安全与权限边界

- TLS/mTLS 用于跨主机通道；设备凭据由 Secret Store/环境注入，不入仓库。
- Topic 使用最小权限 ACL；插件默认无网络、设备和文件写权限。
- 配置、模型、插件包需哈希校验；生产插件应支持签名验证。
- LIVE 需要运行模式令牌、操作员角色、目标飞控绑定和时限。
- 所有控制命令记录请求者、策略版本、输入 revision、门控结果和 Ack。
- GUI 仅请求操作；最终授权由服务端状态机决定。

## 13. 可观测性

统一标签：`service`、`instance_id`、`run_id`、`trace_id`、`vehicle_id`、`plugin_version`、`mode`。

- Logs：结构化 JSON，禁止记录密码、令牌和原始个人敏感数据。
- Metrics：吞吐、P50/P95/P99、队列深度、数据年龄、插件错误、决策计数、控制 Ack。
- Traces：从 Sensor 到 Decision 的跨服务 trace；高频原始数据按比例采样，风险/决策全量。
- Health：`liveness` 仅表示进程活；`readiness` 还要验证依赖、插件和数据新鲜度。

## 14. 外部依赖

- Python 3.11+；Pydantic 或等价 Schema 校验；`asyncio`。
- PyQt6；地图/3D 渲染适配器。
- SQLite（开发）、PostgreSQL（业务/元数据）、InfluxDB（高频时序）。
- 可选 MQTT/NATS/ROS2；MAVLink/PX4/ArduPilot 适配器。
- ONNX Runtime/TensorRT/OpenVINO 等通过模型运行时端口封装。

依赖版本必须锁定；业务代码不能直接散落调用厂商 SDK。

## 15. 验收标准

1. 架构依赖测试证明领域层不导入适配器或 GUI。
2. 同一契约测试可分别运行模拟雷达与真实雷达驱动。
3. 在任一业务插件终止时，宿主捕获故障并在 2 秒内发布健康事件。
4. 数据面高负载不阻塞决策/控制 Topic。
5. `REPLAY` 模式连接真实控制端点时启动必须失败。
6. 所有 Decision 可追踪到输入事件、Twin revision、配置和插件版本。
7. 进程合并/拆分不改变领域消息。

## 16. 测试方法

- 架构静态依赖测试和循环依赖检测。
- Event Bus 契约、顺序、去重、重连、拥塞测试。
- 插件生命周期、崩溃隔离、影子运行和回滚测试。
- 仿真时间、墙钟和重放时钟一致性测试。
- 进程 kill、网络分区、数据库不可用、消息风暴等故障注入。
- `SIM -> HIL -> LIVE` 权限和安全状态机测试。
