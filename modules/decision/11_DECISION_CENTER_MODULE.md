# 智能决策中心模块

## 1. 模块目标

综合任务、感知/数字孪生、天气、风险、路径、能源、通信和系统健康，在确定性安全状态机中选择 `CONTINUE/AVOID/HOLD/RETURN/LAND/ABORT` 策略，生成带原因、有效期和前置条件的 `DecisionMessage`。

## 2. 模块职责

- 对候选路径和当前任务策略进行仲裁。
- 执行优先级、滞回、冷却时间和冲突消解。
- 管理飞行策略状态机和任务阶段。
- 输出结构化解释、输入引用和执行前置条件。
- 调用独立 Safety Gate 进行最终授权。
- 监控 Decision → Control Ack，处理拒绝/超时反馈。

不负责底层姿态/电机控制。Decision Center 提议策略；Safety Gate 授权；Control Gateway 执行。

## 3. 输入

| 输入 | 要求 |
|---|---|
| `risk.update` | 未过期、目标车辆/任务匹配 |
| `path.proposed/selected/failed` | validator 通过、revision 匹配 |
| Twin/Vehicle State | 位置、模式、armed、执行状态、新鲜度 |
| Mission State | 目标、阶段、优先级、允许动作、备降点 |
| Weather/Health/Link | 关键失效和限制 |
| Operator Command | 鉴权、幂等、审计；不能绕过硬安全规则 |
| Decision Policy | 版本、规则集哈希、车辆/任务适用范围 |

## 4. 输出

- `decision.proposed`：符合 [Decision Message](../../03_INTERFACE_SPEC.md#9-decision-message)。
- `decision.authorized`/`decision.rejected`：由 Safety Gate 填充授权结果。
- `decision.transition`：状态机前后状态和原因。
- `mission.state.changed`：任务阶段更新。
- `health.update`、Audit Record。

## 5. 策略优先级

默认安全优先级（高到低）：

```text
ABORT / immediate LAND
        >
LAND at safe site
        >
RETURN
        >
HOLD
        >
AVOID
        >
CONTINUE
```

优先级不是简单固定动作排序：例如当前区域无法安全悬停时 `HOLD` 不能覆盖可行 `AVOID`。决策规则使用上下文前置条件和可行性。

## 6. 状态机

```text
              +--------------------+
              |                    v
IDLE -> READY -> CONTINUING -> AVOIDING -> CONTINUING
           |          |          |
           |          v          v
           +-------> HOLDING <----+
                       |
                 +-----+------+
                 v            v
             RETURNING      LANDING
                 |            |
                 +------> LANDED

Any active state -- unrecoverable safety condition --> ABORTING
```

### 6.1 典型转换

| 当前状态 | 条件 | 动作/下一状态 |
|---|---|---|
| CONTINUING | collision HIGH，valid alternate path | AVOID → AVOIDING |
| AVOIDING | risk LOW 且回归路径 valid 达到滞回时间 | CONTINUE |
| 任意活动状态 | path/risk 暂时过期且可安全悬停 | HOLD |
| 任意活动状态 | 能源/任务/链路策略要求返航，返航可行 | RETURN |
| 任意活动状态 | 返航不可行但安全着陆点可达 | LAND |
| 任意活动状态 | 无安全路径且碰撞迫近/系统不可控 | ABORT/飞控 failsafe |

每个转换必须声明 guard、超时、进入/退出动作和回滚/备选动作。

## 7. 内部结构与决策周期

```text
Trigger
  -> collect coherent context
  -> validate freshness/revision
  -> evaluate hard safety rules
  -> evaluate policy plugin
  -> resolve action/path
  -> build explanation/preconditions/expiry
  -> publish proposed
  -> Safety Gate validate
  -> publish authorized/rejected
  -> Control Gateway + Ack monitor
```

同一 context 的重复 trigger 使用 `(twin_revision,risk_id,path_id,policy_hash)` 去重。安全等级上升绕过冷却。

## 8. 决策规则基线

| 条件 | 最低策略 |
|---|---|
| 碰撞 CRITICAL 且有 valid 避障路径 | `AVOID` |
| 碰撞 CRITICAL 且无可行避障 | `HOLD/LAND/ABORT` 中选择可行最小风险动作 |
| 能源不足以完成任务但可返航 | `RETURN` |
| 能源不足以返航但可达备降点 | `LAND` |
| 控制链路超过任务阈值失联 | 任务预配置 `RETURN/LAND/HOLD` |
| 关键定位/姿态过期 | 禁止新路径控制，进入飞控/任务 failsafe |
| 天气超包线且退出路径可行 | `RETURN` 或 `AVOID` |
| Risk UNKNOWN | 不允许新 `CONTINUE`，请求 HOLD/重评估或更保守动作 |

具体策略按 vehicle/mission policy 配置，但不能把硬安全条件改为 fail-open。

## 9. Safety Gate

Safety Gate 是独立、最小化、规则优先的组件，检查：

1. mode/vehicle/control endpoint 绑定；
2. Decision 未过期且未重复执行；
3. Risk、Path、Twin revision 一致且仍有效；
4. Path validator 结果和硬约束；
5. Vehicle State/Control Link 新鲜；
6. 动作在当前飞行模式、任务和车辆能力内；
7. LIVE 令牌、操作权限和策略签名；
8. 上一控制命令状态允许新命令。

任何检查异常均 reject（fail-closed），记录 `failures[]`。Safety Gate 不调用 AI 模型。

## 10. 插件接口

```python
class DecisionPolicy(Plugin, Protocol):
    async def decide(self, context: DecisionContext) -> DecisionProposal: ...

class SafetyRule(Protocol):
    def evaluate(self, proposal: DecisionProposal, context: SafetyContext) -> RuleResult: ...
```

学习型策略只能提出候选，不得绕过规则基线和 Safety Gate。影子策略输出写入评估 Topic，不能授权。

## 11. 冲突、滞回与抖动控制

- 更高安全严重度动作抢占低严重度动作。
- 同级动作比较可行性、风险上界、能源和任务代价。
- 风险下降需持续 `recovery_hold_s` 才回到 Continue。
- 已下发 AVOID 在最小执行窗口内不因轻微评分变化反复切换。
- 新 CRITICAL 或路径失效可立即中断上述窗口。
- 操作员命令与自动策略冲突时，服务端按权限/安全规则仲裁并解释。

## 12. Ack 与执行监督

Decision 状态：`PROPOSED -> AUTHORIZED -> SENT -> ACCEPTED -> EXECUTING -> COMPLETED`，任何阶段可 `REJECTED/EXPIRED/FAILED`。

- Ack timeout 到达后按幂等键重试有限次数。
- 飞控确认接受不代表执行完成；Vehicle State 必须验证实际响应。
- 实际状态偏离 path/策略触发新 Risk/Decision，而不是无限重发旧命令。

## 13. 配置示例

```yaml
decision:
  policy: baseline_safety_policy@1.0.0
  tick_hz: 10
  recovery_hold_s: 2
  command_ack_timeout_ms: 200
  max_command_retries: 2
  stale_ms:
    vehicle_state: 100
    risk: 500
    local_path: 500
  mission_defaults:
    link_loss_action: RETURN
```

## 14. 错误与降级

| 情况 | 行为 |
|---|---|
| Policy 插件失败 | 使用规则基线 |
| 输入不一致/过期 | 不授权新动作；触发安全策略/重评估 |
| Safety Gate 不可用 | fail-closed；Control Gateway 不接收未授权消息 |
| Ack 超时 | 有限幂等重试，随后通信失效策略 |
| Decision 输出非法 | 隔离，回退基线 |
| 审计存储不可用 | 写有界本地安全 WAL；缓冲耗尽禁止扩大操作权限 |

## 15. 外部依赖

EventBus、Clock、Twin/Risk/Planning、Mission Repository、Policy/Config Repository、Audit、Control Gateway、Metrics。Decision 不直接使用传感器 SDK/厂商飞控 SDK。

## 16. 验收标准

1. 所有标准场景产生预期动作和 reason code，关键安全漏判为 0。
2. 同一 context 和 policy 输出确定性一致。
3. P95 决策计算 ≤ 20 ms（不含规划）。
4. 过期/未验证路径授权数为 0。
5. `REPLAY/SIM` 向真实 endpoint 发命令数为 0。
6. 每个授权 Decision 可追踪到 Risk、Path、Twin、Policy、操作员和 Ack。
7. 策略插件崩溃后 500 ms 内规则基线生效。
8. 重复 Decision/Control 消息不产生重复副作用。

### 16.1 Phase 5 可执行基线

- `DecisionCenter` 已实现 Continue/Avoid/Hold/Return/Land/Abort 硬规则、
  Twin revision 校验、上下文去重、车辆能力降级和 Avoid 恢复滞回。
- `SafetyGate` 独立校验 Decision/Risk/Path/Vehicle 的身份、时效、revision、
  路径验证、链路、车辆能力和 endpoint 模式绑定；异常统一 fail-closed。
- `SimulatedControlGateway` 只接收 `SIM` 下已授权 Decision，以 Decision ID 幂等，
  输出 `control.command/1.0` 和 `control.ack/1.0`，不包含真实飞控连接能力。
- 标准 SIL 套件覆盖 Continue、Avoid、Return、Land、Hold、过期路径、SIM→REAL
  拒绝、Decision ID 复用和重复控制副作用。
- 当前 Ack 为同步模拟完成；超时/分阶段执行监督和真实飞控映射留待韧性与 HIL 阶段。

## 17. 测试方法

- 状态机状态/转换/guard/超时的模型化测试。
- 决策表全组合与边界测试，检查无规则空洞和冲突。
- 风险抖动、路径切换、过期竞态、同级冲突和操作员命令测试。
- Safety Gate 每条规则的 allow/deny/异常 fail-closed 测试。
- Ack 丢失、重复、延迟、拒绝和实际状态不响应测试。
- 全闭环场景：Continue、Avoid、Hold、Return、Land、Abort。
