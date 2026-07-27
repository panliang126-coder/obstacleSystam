# 系统测试计划

## 1. 模块目标

建立从纯函数、Schema、插件、服务、仿真闭环到 HIL/LIVE 准入的分层测试体系，用可重复场景和可量化指标证明系统满足功能、实时性、鲁棒性、安全性与可追溯要求。

## 2. 测试原则

- 模拟优先、真实设备后置；没有 SIL/HIL 证据不进入 LIVE。
- 核心契约测试对真实/模拟实现共享。
- 测试使用固定 run manifest、场景、种子和 Clock。
- 正常路径、边界、异常、恢复和不可恢复路径同等重要。
- 安全断言默认 fail-closed；测试失败不能通过重跑掩盖。
- 性能报告给出硬件、版本、负载和 P50/P95/P99。
- 每个缺陷新增最小回归用例或场景。

## 3. 测试层级

| 层级 | 范围 | 依赖 | 运行频率 | 退出标准 |
|---|---|---|---|---|
| L0 静态 | lint、类型、依赖、Secret、Schema breaking | 无外部服务 | 每次提交 | 0 error |
| L1 单元 | 领域纯函数、状态机、算法组件 | fake clock/RNG | 每次提交 | 核心 ≥85% 行覆盖 |
| L2 契约 | Message、Driver、Plugin、Repository、EventBus | fixture/container | 每次提交 | 所有实现共享套件通过 |
| L3 集成 | 模块+Broker+DB+Artifact | 容器服务 | PR/每日 | 数据和故障路径通过 |
| L4 SIL | 完整模拟闭环 | Scenario Simulator | PR 关键/每日全量 | 场景断言全通过 |
| L5 性能/韧性 | 峰值、长稳、故障注入 | 目标硬件/近似环境 | 每日/里程碑 | 达到 SLA、无泄漏 |
| L6 HIL | 真实飞控/传感器测试台 | 隔离硬件 | 版本候选 | 设备/安全准入通过 |
| L7 现场 | 受控场地和审批 | 真实平台 | 正式准入 | 安全计划全部满足 |

## 4. 测试环境

| 环境 | 用途 | 控制权限 |
|---|---|---|
| Dev | 单元、快速集成、SQLite/InProcess Bus | 无真实设备 |
| CI | 容器化 PostgreSQL/InfluxDB/Broker、SIL | 明确禁止真实 endpoint |
| Performance | 与边缘硬件相同 CPU/GPU/NPU | 模拟控制 |
| HIL Lab | 飞控、传感器、网络仿真器、急停 | 物理隔离、受控 |
| Field | 分级场地、观察员、急停、飞控原生 failsafe | 审批后限时 |

环境清单、固件、容器、配置和接线图作为 test artifact 保存。

## 5. 测试数据与场景目录

```text
tests/
├── unit/
├── contract/
│   ├── messages/
│   ├── drivers/
│   ├── plugins/
│   ├── repositories/
│   └── event_bus/
├── integration/
├── scenarios/
│   ├── nominal/
│   ├── obstacles/
│   ├── weather/
│   ├── failures/
│   └── safety/
├── performance/
├── hil/
├── fixtures/
│   ├── valid/
│   ├── invalid/
│   └── compat/
└── golden/
```

数据集记录来源、许可、哈希、划分规则和泄漏检查。训练/验证/测试划分不可因模型调参静默改变。

## 6. 模块测试矩阵

| 模块 | 核心输入 | 核心断言 |
|---|---|---|
| Interface | 六类消息、未知字段/版本 | Schema、单位、兼容、序列化 |
| Simulator | 场景/种子/故障 | 确定性、truth 隔离、契约一致 |
| Perception | 多源数据+truth | 检测/航迹精度、延迟、协方差、降级 |
| Weather | 解析天气场 | 估计误差、coverage、unknown、突变 |
| Twin | 事件序列 | revision、重建、冲突、branch 隔离 |
| Risk | Twin/Path/Health | 分数/等级/解释、单调性、deadline |
| Planning | 约束/动态目标 | 可行、无碰撞、时延、无解解释 |
| Decision | Risk/Path/Policy | 状态机、优先级、授权、幂等 |
| Management | 配置/artifact/健康 | 审批、回滚、兼容、审计 |
| GUI | 事件/命令 | 状态正确、线程、断线、RBAC、性能 |
| Deployment | 镜像/配置/Secret | 可复现、健康、升级/回滚、安全 |

## 7. 核心场景

### 7.1 标称

1. 空域无障碍，任务完整执行，Decision 为 Continue。
2. 单静态障碍，全局路径绕行。
3. 单动态交叉目标，局部 Avoid 后恢复 Continue。
4. 多传感器同一目标，融合航迹稳定且 source refs 完整。

### 7.2 天气

1. 侧风逐步增强至包线边界。
2. 航线上局部雨带/低能见度，规划绕行。
3. 突发阵风触发 Risk/Decision。
4. 天气覆盖缺口不能显示为 LOW。

### 7.3 能源与通信

1. 电量不足完成任务但足够返航 → Return。
2. 无法返航但备降点可达 → Land。
3. 控制链路 jitter/丢包/断链，按任务政策降级。
4. Ack 丢失/重复，命令副作用只执行一次。

### 7.4 系统失效

1. 雷达、相机分别离线和同时离线。
2. 时钟漂移、错误 frame、标定版本不匹配。
3. 感知/风险/规划插件超时或崩溃。
4. Event Bus、PostgreSQL、InfluxDB、对象存储断连。
5. CPU/GPU 饱和和 OOM 回退。
6. GUI 断线不影响闭环；重连无状态空洞。

### 7.5 安全

1. 过期 Risk/Path/Twin 被 Safety Gate 拒绝。
2. REPLAY/SIM 试图连接真实 Control Gateway，启动/授权失败。
3. 非授权用户激活 LIVE/安全阈值/插件失败。
4. 无安全路径时进入 Hold/Land/Abort，不发送非法轨迹。
5. 多故障组合：恶劣天气+传感器丢失+低电量。

## 8. 详细测试模板

每个用例必须包括：

```yaml
id: SIL-COLLISION-001
title: 动态目标交叉避障
requirements: [RISK-COLLISION, PLAN-LOCAL, DECISION-AVOID]
environment: ci-sil
inputs:
  scenario: dynamic-crossing-v1
  seed: 42001
  config: baseline-sil@sha256:...
steps:
  - start run
  - wait for both tracks confirmed
  - observe risk/path/decision/control events
assertions:
  - no_collision
  - risk_level_reaches: HIGH
  - decision_action_seen: AVOID
  - min_clearance_m: ">= 15"
  - end_to_end_p95_ms: "<= 200"
artifacts:
  - run_manifest
  - event_trace
  - metrics
  - scenario_assertions
```

## 9. 接口与契约测试

### 9.1 消息

- 每个 Schema 的最小/完整 valid 示例。
- 缺字段、错误类型、超范围、未知 major、非法时间/UUID/单位。
- minor 版本旧消费者和 upcast。
- JSON ↔ protobuf 语义 round-trip。

### 9.2 Driver

共享套件验证 lifecycle、connect/reconnect、session/sequence、cancel/backpressure、frame/time/calibration/quality、非法源数据。真实 Driver 在没有硬件时可使用厂商录制流，但 HIL 前必须在硬件执行。

### 9.3 Plugin

manifest、兼容、initialize/warmup/health/shutdown、deadline/cancel、非法输出、崩溃隔离、shadow 无副作用、版本回滚。

### 9.4 Repository/Event Bus

幂等、事务、Outbox/Inbox、顺序假设、重连、持久消费、ACL、满队列和 broker failover。

## 10. 性能与稳定性

### 10.1 负载模型

至少覆盖：

- 1/10 台 vehicle；
- 10/100/1000/5000 动态目标；
- 雷达 20 Hz、EO/IR 30 Hz、IMU 200 Hz；
- 标称、2×峰值、10×短时 burst；
- 正常/高延迟存储和 Broker。

### 10.2 指标

- 各阶段 throughput、P50/P95/P99、消息 age、queue depth/drop；
- CPU/GPU/NPU、RAM/VRAM、磁盘/网络；
- 感知/风险/规划/决策质量指标；
- 进程重启和恢复时间；
- 1 h、8 h、24 h soak 的内存/句柄/线程趋势。

出现 deadline miss 必须关联到 run/trace，不能只报告均值。

## 11. 故障注入

使用场景或基础设施代理注入：

- 1–30% 丢包、100–1000 ms 延迟、乱序/重复；
- 服务 kill/restart、节点重启；
- 数据库只读/满磁盘、Broker 分区；
- 时钟偏移/漂移；
- corrupt payload/artifact；
- GPU OOM、推理超时；
- 关键传感器逐个/组合离线。

断言检测时间、状态传播、降级动作、恢复和审计。

## 12. HIL 测试

准入条件：L0–L5 全通过、版本候选冻结、安全评审完成。

HIL 项目：

- PX4/ArduPilot 连接、模式/armed 状态、MAVLink 消息映射；
- setpoint/mission command、Ack、重试和幂等；
- NED↔ENU、时间同步、速率和单位；
- 传感器真实吞吐、断线和标定；
- 飞控原生 failsafe、控制链路丢失和急停；
- 禁止意外螺旋桨/执行器动作的物理防护。

## 13. 现场测试分级

1. 台架、无桨/无执行器危险；
2. 系留/封闭区低能量；
3. 单机视距、静态障碍；
4. 受控动态目标和天气条件；
5. 扩展任务包线。

每级有 go/no-go、天气/空域/人员、急停、观察员、日志和事故处置清单。失败后回到前一级，不自动扩大包线。

## 14. 验收指标

系统基线沿用 [00_MASTER.md](../00_MASTER.md#11-全局质量指标)，模块阈值以各模块文档为准。发布候选还必须：

- 阻断级/高危缺陷为 0；
- 所有安全场景和回归场景通过；
- 核心事件可追溯率 100%；
- flaky 测试不得通过简单重试隐藏，需有 owner/期限；
- 测试报告与 run manifest/artifact hash 完整。

## 15. 缺陷分级

| 级别 | 示例 | 发布处理 |
|---|---|---|
| Blocker | 未授权控制、碰撞漏判、Safety Gate fail-open、数据不可追溯 | 必须修复并重跑全安全集 |
| Critical | 关键降级失败、长期数据/审计丢失、HIL 指令错误 | 必须修复 |
| Major | 模块性能超预算、部分场景错误、恢复不完整 | 默认阻止发布，需正式豁免 |
| Minor | 非关键 UI/诊断问题 | 可带已知问题发布 |

## 16. 测试报告

报告必须包含：build/commit、manifest、环境、场景/seed、通过/失败/跳过、指标分位数、覆盖率、缺陷、与上版比较、artifact 链接、结论和批准人。
