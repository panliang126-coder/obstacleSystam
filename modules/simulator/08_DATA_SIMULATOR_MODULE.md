# 数据模拟器模块

## 1. 模块目标

在没有真实设备或不允许真实飞行的阶段，生成与真实 Driver 完全相同的业务消息，支持实时仿真、历史回放、压力测试和故障注入，为数字孪生驱动的完整闭环提供确定性输入。

## 2. 模块职责

- 模拟飞行器、障碍物、雷达、EO/IR、天气、GNSS/IMU、通信链路和飞控。
- 执行场景脚本、运动模型、传感器模型、噪声/遮挡和设备故障。
- 输出标准 `SensorMessage`、Vehicle State、Control Ack 和 Health。
- 接收 Control Command，使模拟飞行器状态闭环响应。
- 录制/回放真实或仿真事件，支持倍速、暂停、逐步和断点。
- 生成负载、乱序、丢包、延迟、时钟漂移和异常值。

不负责使用“完美真值”替代感知结果；真值使用隔离 Topic，业务模块默认无权订阅。

## 3. 运行模式

| 模式 | 时钟 | 输出节奏 | 控制输入 | 用途 |
|---|---|---|---|---|
| `REALTIME` | SimClock 与墙钟 1:1 | 实时 | 模拟控制命令 | 交互演示/SIL |
| `ACCELERATED` | SimClock 倍速 | 按仿真时间 | 模拟控制命令 | 批量回归 |
| `STEP` | 手动推进 | 每 tick | 可选 | 调试/断言 |
| `REPLAY` | 记录时间 | 0.1x–N x | 默认禁用 | 缺陷复现 |
| `STRESS` | 受控/尽快 | 目标吞吐 | 可选 | 性能与背压 |

所有业务超时都读取注入的 Clock。`ACCELERATED/REPLAY` 不允许调用真实 Control Gateway。

## 4. 输入

- 场景清单、随机种子、地图/地形和初始实体。
- 传感器型号与安装标定、噪声、帧率、视场和探测概率。
- 天气场、时间演化和区域事件。
- 飞行器动力学/能源/通信参数。
- 故障时间线。
- `control.command`（仅模拟控制适配器）。
- 回放清单和事件/对象存储引用。

## 5. 输出

| 输出 | Topic | 说明 |
|---|---|---|
| 模拟传感器 | `sensor.normalized.*` | 与真实 Driver 同 Schema |
| Vehicle State | `vehicle.state` | 与飞控适配器同 Schema |
| Control Ack | `control.ack` | 模拟执行/拒绝/超时 |
| 模拟设备健康 | `health.update` | 离线、漂移、过热等 |
| 仿真真值 | `simulation.truth.*` | 仅测试/评估 ACL |
| 生命周期 | `simulation.run.*` | started/paused/completed/failed |

## 6. 内部结构

```text
Scenario Loader
      |
SimClock + Seed Manager
      |
World Dynamics Engine <------- Simulated Control Gateway
      |
Truth State Store
  +---+---------------+----------------+
  |                   |                |
Sensor Models     Weather Model    Link/Failure Model
  |                   |                |
Driver-compatible Adapters + Envelope Builder
                      |
                 Event Bus
```

### 6.1 随机种子

使用主种子按组件名称派生稳定子种子，例如：

```text
seed(run)
├── vehicle/uav-001
├── sensor/radar-front-01/noise
├── sensor/camera-01/dropout
├── weather/gust
└── link/control
```

新增不相关组件不能改变现有组件随机序列。使用的算法/库版本写入 run manifest。

### 6.2 Truth 隔离

- `simulation.truth.*` 与 `sensor.*` 使用独立 ACL。
- 感知、天气、风险、规划的生产配置不能订阅 truth。
- 测试评估器可同时读取输出和 truth 计算误差。
- CI 含依赖测试，防止业务模块导入 simulator truth 类型。

## 7. 场景格式

```yaml
scenario:
  id: dynamic-crossing-v1
  duration_s: 600
  seed: 42001
  frame:
    id: site-alpha-enu-v1
    origin_wgs84: [31.2304, 121.4737, 100.0]
  vehicles:
    - id: uav-001
      model: multirotor-basic@1.0
      initial_enu_m: [0, 0, 20]
      initial_velocity_m_s: [0, 0, 0]
  obstacles:
    - id: intruder-01
      type: UAV
      trajectory: {kind: waypoints, points: [[100, 10, 20], [-30, 10, 20]]}
  sensors:
    - id: radar-front-01
      driver: radar-sim@1.0
      rate_hz: 20
      config: {range_m: 200, azimuth_fov_deg: 120, detection_probability: 0.95}
  weather:
    model: grid-weather@1.0
    base_wind_enu_m_s: [4, 0, 0]
  failures:
    - at_s: 120
      target: sensor/radar-front-01
      action: dropout
      duration_s: 5
  assertions:
    - type: no_collision
    - type: decision_seen
      action: AVOID
      within_s: [5, 125]
```

Scenario Schema 必须验证 ID 唯一、时间范围、单位、frame、引用和参数边界。

## 8. 设备模型

### 8.1 雷达

范围/FOV、更新率、分辨率、探测概率、虚警、遮挡、RCS 近似、距离/角度/径向速度噪声和扫描延迟。输出完整扫描或检测，与真实雷达子 Schema 相同。

### 8.2 EO/IR

相机内外参、分辨率、帧率、曝光/热响应、运动模糊、天气衰减、遮挡和编码延迟。大图像写对象存储，Sensor Message 使用 `raw_ref`。

### 8.3 GNSS/IMU

频率、白噪声、bias、random walk、漂移、跳变、失锁、延迟；GNSS 可模拟多路径和干扰。IMU 输出必须说明 frame 和单位。

### 8.4 天气

均匀/栅格风、阵风、剪切、降水、能见度、温湿度及随时间演化。真值与带噪观测分开。

### 8.5 通信

延迟、jitter、带宽、丢包、重复、乱序、断链和恢复。控制 Ack 的行为符合真实 Control Gateway 契约。

## 9. 回放

回放清单：

```json
{
  "source_run_id": "...",
  "event_range": {"from": "...", "to": "..."},
  "topics": ["sensor.normalized.*", "vehicle.state"],
  "artifacts": [{"uri": "...", "sha256": "..."}],
  "schema_versions": {"sensor": "1.0"},
  "config_hash": "sha256:...",
  "plugin_manifest_hash": "sha256:..."
}
```

- 回放前校验所有哈希和 Schema upcaster。
- 输出使用新 `run_id`，保留 `replay_of_event_id`。
- 可配置保留原始事件间隔或按统一仿真 tick 重采样。
- 对缺失/损坏 artifact 必须失败或明确跳过，不能静默填零。

## 10. 故障注入

故障类型：

- 设备：断线、卡死、重复帧、错误 CRC、过热、标定漂移；
- 数据：偏置、噪声、离群、时间戳跳变、frame 错误；
- 网络：延迟、丢包、乱序、分区；
- 计算：插件超时、OOM、进程重启；
- 环境：突发阵风、能见度下降、移动障碍群；
- 控制：Ack 丢失、命令拒绝、执行滞后。

故障注入事件写入审计/真值流，便于断言检测时间和降级行为。

## 11. 插件接口

```python
class SimulatorDriver(SensorDriver, Protocol):
    def bind(self, truth: TruthStatePort, clock: SimClock, rng: RandomStream) -> None: ...

class DynamicsPlugin(Plugin, Protocol):
    def step(self, state: VehicleTruth, control: SimControl, dt_s: float) -> VehicleTruth: ...

class ScenarioAssertion(Protocol):
    def observe(self, event: Envelope, truth: TruthSnapshot) -> None: ...
    def result(self) -> AssertionResult: ...
```

## 12. 性能与背压

- 仿真 tick 默认 10 ms，可按场景调整。
- 每个 sensor model 独立调度，不因低频天气阻塞 IMU。
- STRESS 模式目标吞吐明确记录，不用无界并发。
- 仿真落后于实时超过阈值时发布 lag metric；REALTIME 模式不得静默跳过动力学步骤。

## 13. 外部依赖

Clock、EventBus、Scenario/Artifact Repository、数值/几何/渲染可选引擎。仿真核心不得依赖 GUI；高保真引擎通过 Adapter 接入。

## 14. 验收标准

1. 每种真实 Driver 至少一个模拟实现通过同一 Driver Contract Test。
2. 相同场景、种子、版本和执行模式的规范化事件哈希一致。
3. 业务进程无法订阅 truth Topic。
4. 可注入附件列出的所有基础故障，并能量化检测/恢复时间。
5. 1x REALTIME 连续运行 1 小时，无时钟漂移累积和无界内存增长。
6. STRESS 达到标称峰值 2 倍，系统背压行为符合 `02_DATA_FLOW.md`。
7. 回放不会连接真实 Control Gateway。

## 15. 测试方法

- 单元测试传感器噪声统计、动力学积分、种子派生和场景校验。
- 对模拟/真实 Driver 运行共享契约测试。
- 用 truth 计算感知/天气/规划误差，不让 truth 进入被测模块。
- 场景回归：静态障碍、交叉目标、追尾、障碍群、突发天气、断链、低电量。
- 测试暂停/继续/倍速/逐步时事件顺序和 timer 行为。
- 记录 CPU/GPU/内存、实时因子、事件吞吐和队列峰值。
