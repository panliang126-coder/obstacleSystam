# 低空智能感知与自主决策系统：工程总控

> English name: Low Altitude Intelligent Perception and Decision System<br>
> 文档状态：Baseline v1.0<br>
> 适用阶段：SIL 仿真、数据回放、HIL 联调、真实设备接入<br>
> 最后更新：2026-07-27

## 1. 本文件的权威性

本文件是项目的总控入口。任何开发者或 Codex Agent 在修改代码前，必须依次阅读：

1. 本文件；
2. [统一接口规范](03_INTERFACE_SPEC.md)；
3. 所修改模块对应的设计文档；
4. [Codex Agent 开发规范](codex/16_CODEX_AGENT_RULES.md)。

约束冲突时，优先级为：安全约束 > `03_INTERFACE_SPEC.md` > 本文件 > 模块文档 > 当前实现。接口或安全约束需要变更时，必须先修改文档、增加版本和兼容/迁移说明，再修改代码。

## 2. 项目背景

项目面向无人机、eVTOL 和低空机器人，在天气变化、动态障碍、通信波动和传感器不完备条件下，形成“感知—理解—评估—规划—决策—控制”的自主闭环。第一阶段以数字孪生和模拟器建立可重复、可测试的完整闭环；随后用接口完全一致的真实驱动替换模拟驱动，上层业务逻辑不因设备替换而改变。

本项目是决策辅助与自主系统软件底座，不把单一算法或单一传感器视为可信安全源。任何飞行控制输出都必须经过决策中心、安全门控和飞控自身保护机制。

## 3. 系统目标与边界

### 3.1 目标

- 接入雷达、EO/IR、气象、GNSS/IMU、声学、电磁及飞控状态等多源数据。
- 对数据统一时间、坐标、质量和版本语义，输出稳定的目标航迹与环境状态。
- 维护真实世界的可回放数字孪生状态，支持预测和方案推演。
- 对碰撞、天气、能源、通信和系统健康风险进行解释性评估。
- 提供全局规划、局部避障和动态重规划，并生成可审计的飞行策略。
- 通过 PyQt6 前端显示系统、地图、目标、风险、路径、孪生和日志。
- 同一业务链支持 `SIM`、`REPLAY`、`HIL`、`LIVE` 四种运行模式。
- 通过插件、驱动适配器和版本化事件协议实现算法与设备的动态替换。

### 3.2 非目标

- 不绕过 PX4/ArduPilot 的姿态控制、执行器控制和硬件失效保护。
- 不在未完成 HIL、安全评审和现场授权前向真实飞行器发送可执行指令。
- 不承诺仅凭单一感知模型达到适航或认证要求。
- v1 不实现空域审批、运营调度和民航监管平台本身，只保留适配接口。

## 4. 不可破坏的设计原则

1. **统一数据契约**：跨模块只传输 [统一接口规范](03_INTERFACE_SPEC.md) 定义的版本化消息。
2. **事件驱动**：业务模块通过 Event Bus 解耦，禁止跨模块读取对方私有数据库或直接调用内部对象。
3. **插件化**：感知、天气、风险、规划、决策算法均通过稳定协议加载，插件必须声明名称、语义版本、能力和兼容协议版本。
4. **驱动同构**：`RadarDriver` 与 `RadarSimulator` 等真实/模拟实现遵守同一端口接口和消息 Schema。
5. **模拟优先**：新能力必须先在确定性仿真和回放中验证，再进入 HIL/LIVE。
6. **安全默认**：缺失、过期、冲突或低质量数据不能被解释为“无风险”；系统必须降级或进入安全策略。
7. **全链路可追溯**：每条消息携带 `event_id`、`trace_id`、时间、来源、Schema 版本和质量信息。
8. **单位与坐标明确**：SI 单位；角度为 degree；同时支持 WGS-84 和局部 ENU，任何坐标转换显式记录参考原点。
9. **确定性回放**：给定相同场景、配置、种子和插件版本，关键决策结果应可复现。
10. **控制隔离**：只有 Control Gateway 可连接真实飞控；UI、感知、规划插件无权直接发出飞控命令。

## 5. 总体架构

```text
 Real Sensors / Simulators / Replay Sources
                    |
        Driver Adapter + Validation
                    |
         Time Sync / Frame Transform
                    |
              Event Bus
       +------------+-------------+
       |                          |
 Perception Pipeline       Weather Pipeline
       |                          |
       +--------> Digital Twin <--+
                        |
                 Risk Assessment
                        |
              Global/Local Planning
                        |
                 Decision Center
                        |
                   Safety Gate
                        |
         Control Gateway / Flight Controller

 Every stage --> Event Store / Metrics / Audit
 Event Bus    --> PyQt GUI / External Adapters
```

详细分层、进程边界和部署视图见 [系统总体架构](01_SYSTEM_ARCHITECTURE.md)，端到端顺序和时序见 [数据流](02_DATA_FLOW.md)。

## 6. 模块清单

| 编号 | 模块 | 核心职责 | 主要输入 | 主要输出 | 设计文档 |
|---|---|---|---|---|---|
| 01 | 系统架构 | 分层、进程、依赖、安全边界 | 需求与约束 | 架构基线 | [01](01_SYSTEM_ARCHITECTURE.md) |
| 02 | 数据流 | 时序、背压、失败流、回放 | 全部消息 | 流程基线 | [02](02_DATA_FLOW.md) |
| 03 | 接口层 | Schema、Topic、插件协议 | 原始/业务数据 | 统一契约 | [03](03_INTERFACE_SPEC.md) |
| 04 | 数据层 | 实时、历史、模型、配置存储 | 事件与元数据 | 查询与回放数据 | [04](04_DATABASE_DESIGN.md) |
| 05 | 感知 | 检测、跟踪、分类、融合 | `SensorMessage` | `TargetMessage` | [05](modules/perception/05_PERCEPTION_MODULE.md) |
| 06 | 天气 | 天气融合与环境风险因子 | 气象传感器/服务 | `EnvironmentMessage` | [06](modules/weather/06_WEATHER_MODULE.md) |
| 07 | 数字孪生 | 世界状态、预测、推演 | 目标、天气、飞行器状态 | Twin Snapshot/Prediction | [07](modules/digital_twin/07_DIGITAL_TWIN_MODULE.md) |
| 08 | 模拟器 | 设备、场景、故障、回放 | 场景配置/种子 | 与真实驱动同构的消息 | [08](modules/simulator/08_DATA_SIMULATOR_MODULE.md) |
| 09 | 风险 | 多维风险计算和解释 | Twin/目标/环境/健康 | `RiskMessage` | [09](modules/risk/09_RISK_ASSESSMENT_MODULE.md) |
| 10 | 规划 | 全局、局部、重规划 | Twin/Risk/任务 | `PathMessage` | [10](modules/planning/10_PATH_PLANNING_MODULE.md) |
| 11 | 决策 | 策略仲裁、安全门控前置 | Path/Risk/任务/健康 | `DecisionMessage` | [11](modules/decision/11_DECISION_CENTER_MODULE.md) |
| 12 | 系统管理 | 配置、日志、模型、插件、健康 | 管理命令/指标 | 状态、审计、告警 | [12](modules/management/12_SYSTEM_MANAGEMENT_MODULE.md) |
| 13 | PyQt GUI | 监视、控制授权、回放 | Event Bus/查询 API | 操作命令/可视化 | [13](frontend/13_FRONTEND_GUI_MODULE.md) |
| 14 | 测试 | 单元、契约、仿真、HIL、系统测试 | 场景和指标 | 测试证据 | [14](test/14_TEST_PLAN.md) |
| 15 | 部署 | 容器、边缘、GPU/NPU、运维 | 构建产物 | 可运行环境 | [15](deploy/15_DEPLOYMENT.md) |
| 16 | Agent 规范 | 开发流程与禁止事项 | 任务上下文 | 可审查变更 | [16](codex/16_CODEX_AGENT_RULES.md) |

## 7. 依赖关系与允许的调用方向

```text
interface/domain  <-- 所有模块只依赖稳定契约
       ^
       |
drivers/simulator --> event_bus --> perception/weather
                                      |
                                      v
                                digital_twin
                                      |
                                      v
                                    risk
                                      |
                                      v
                                  planning
                                      |
                                      v
                                  decision
                                      |
                                      v
                              control_gateway

management、storage、observability、frontend 通过公共端口横切接入。
```

- `interface/domain` 不依赖任何业务模块。
- 业务模块不能导入其他模块的 `internal` 或具体插件实现。
- 数据库通过 Repository 端口访问；消息代理通过 Event Bus 端口访问。
- GUI 不能访问模块内存对象，只订阅消息或调用受控查询/命令 API。
- 循环依赖是架构缺陷，必须通过契约事件或端口拆分消除。

## 8. 运行模式

| 模式 | 数据源 | 时钟 | 控制输出 | 典型用途 |
|---|---|---|---|---|
| `SIM` | 模拟驱动 | 可加速仿真时钟 | 仅模拟飞控 | 功能开发、场景测试 |
| `REPLAY` | 事件日志/数据集 | 记录时间，可倍速 | 禁止真实输出 | 缺陷复现、离线评估 |
| `HIL` | 混合真实与模拟 | 墙钟/硬件时钟 | 仅测试台授权端点 | 硬件联调 |
| `LIVE` | 真实驱动 | UTC+单调时钟 | 需双重授权和安全门控 | 现场运行 |

运行模式在进程启动后不可静默切换。切换到 `HIL`/`LIVE` 必须生成审计事件；`REPLAY` 永远不能连接真实控制端点。

## 9. 建议代码骨架

```text
src/low_altitude_ai/
├── domain/                 # 消息模型、枚举、单位、错误
├── ports/                  # EventBus、Repository、Clock、Driver、Plugin 协议
├── adapters/
│   ├── event_bus/
│   ├── storage/
│   ├── sensors/
│   └── flight_control/
├── perception/
├── weather/
├── digital_twin/
├── simulator/
├── risk/
├── planning/
├── decision/
├── management/
└── app/                    # 组合根与进程入口
frontend/
configs/
schemas/
proto/
tests/
deploy/
```

该骨架是实现目标，不要求一次性创建空壳目录。只有任务需要时才创建对应文件。

## 10. 开发阶段与退出门槛

### Phase 1：架构与契约

- 完成本文件及 01–04 文档。
- 固化消息信封、六类核心消息、Topic 和版本策略。
- 退出门槛：Schema 示例全部通过验证；依赖图无循环；关键 ADR 已记录。

### Phase 2：模拟器与数字孪生

- 实现确定性场景、模拟驱动、事件总线、Twin State Store。
- 退出门槛：可在无真实设备情况下运行一条完整的“传感器→Twin”数据链；同种子回放一致。

### Phase 3：感知与天气

- 实现最小可用检测/跟踪/融合和环境场估计插件。
- 退出门槛：基准数据集达到模块文档阈值；丢包和乱序时可降级。

### Phase 4：风险与规划

- 实现规则基线风险引擎、全局规划和局部避障。
- 退出门槛：关键场景无碰撞；规划时延满足预算；输出带解释与约束。

### Phase 5：决策闭环

- 实现策略仲裁、安全状态机和模拟控制网关。
- 退出门槛：`Continue/Avoid/Return/Land/Hold` 场景测试全部通过，命令可追溯。

### Phase 6：GUI 与系统管理

- 实现实时监控、历史回放、配置/插件/健康视图。
- 退出门槛：GUI 卡顿、权限、回放一致性和告警确认测试通过。

### Phase 7：HIL 与真实设备

- 逐个引入 PX4/ArduPilot、ROS2、雷达、EO/IR、气象设备适配器。
- 退出门槛：契约测试与模拟实现一致；完成 HIL、失效注入、安全评审和回滚演练。

## 11. 全局质量指标

以下为 v1 基线，场景或硬件不同可通过受控配置收紧，但不得无记录放宽：

| 指标 | 基线 |
|---|---|
| 核心事件 Schema 合法率 | 100% |
| 关键事件端到端 P95 延迟（采集至决策，边缘部署） | ≤ 200 ms |
| 局部重规划 P95 | ≤ 100 ms |
| 决策链可追溯率 | 100% |
| 10 分钟动态避障标准场景碰撞数 | 0 |
| 关键进程异常检测时间 | ≤ 2 s |
| 决策输入过期后的安全降级触发时间 | ≤ 500 ms |
| 1 小时标称负载运行 | 无未处理异常、无无界内存增长 |
| 固定种子关键决策重放一致率 | 100% |
| 单元+契约测试覆盖率 | 核心领域代码行覆盖率 ≥ 85% |

## 12. Codex Agent 最小工作协议

每次变更必须：

1. 明确任务影响的模块、接口版本和运行模式。
2. 先搜索现有实现和测试，保留用户已有改动。
3. 仅修改任务所需文件，不顺手重构无关区域。
4. 新增/修改消息时同步 Schema、示例、契约测试和本文档索引。
5. 新设备先实现 Driver 端口和 Simulator/Mock，再接入真实 SDK。
6. 运行最小相关测试；高风险变更还需场景回放和故障注入。
7. 汇报变更、验证证据、剩余风险和明确未完成项。

完整规则见 [16_CODEX_AGENT_RULES.md](codex/16_CODEX_AGENT_RULES.md)。

## 13. 基线变更规则

需要修改下列内容时，必须记录 Architecture Decision Record（ADR）：

- 六类核心消息字段的删除、重命名或语义变化；
- 坐标系、时间、单位、风险等级或决策状态机；
- Event Bus、数据库或进程边界的替换；
- 真实控制权限和安全降级条件；
- 全局验收阈值的放宽。

ADR 至少包含：背景、选择、备选项、兼容影响、迁移计划、回滚方法和批准人。
