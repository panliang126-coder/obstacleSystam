# ADR 0005: Phase 5 独立安全门控与模拟控制隔离

- 状态：Accepted
- 日期：2026-07-27

## 背景

Phase 5 需要把 Risk、Path、Vehicle State 和任务能源策略闭合为可追溯的
Decision → Authorization → Control Command → Ack 链路。决策策略可能扩展为插件，
因此不能让策略本身拥有最终控制权限。

## 决策

1. `DecisionCenter` 只产生 `PENDING` 提议，并在确定性状态机中执行硬规则、动作优先级、
   上下文去重和风险恢复滞回。
2. 独立 `SafetyGate` 重新检查时效、车辆绑定、Risk/Path/Twin revision、路径验证、
   车辆能力、链路和运行模式；任何解析或检查异常都生成结构化拒绝。
3. `SIM/REPLAY` 决策永不授权到真实 endpoint。Phase 5 仅提供只接受 `SIM` 决策的
   `SimulatedControlGateway`。
4. 控制命令使用 Decision ID 作为幂等域；重复授权事件返回同一 Command/Ack，
   Decision ID 被其他事件复用时拒绝，避免重复副作用或缓存混淆。
5. 在首次实现前固化 `vehicle.state/1.0`、`mission.command/1.0`、
   `control.command/1.0` 和 `control.ack/1.0` 的 JSON Schema、样例与 protobuf。

## 结果

- 学习型或规则型策略都不能绕过 Safety Gate。
- Continue、Avoid、Return、Land、Hold 使用同一条审计和控制链。
- SIL 可证明幂等、确定性和真实控制隔离；真实飞控仍需 Phase 7 HIL 与安全准入。

## 局限

当前模拟网关立即返回 `COMPLETED`，尚未模拟分阶段 Ack、超时重试和真实车辆响应验证。
这些能力必须在后续韧性测试和 HIL 中补齐，不能从 SIL 结果推导真实飞行安全性。
