# 风险评估模块

## 1. 模块目标

结合数字孪生、天气、动态目标、能源、通信和系统健康，对车辆、任务、路径和区域计算可解释、可追溯、带有效期的风险评分，输出 `RiskMessage` 供规划和决策使用。

## 2. 模块职责

- 计算天气、目标/碰撞、能源、通信和系统五类风险。
- 对未来 horizon 预测风险，而非只判断当前几何距离。
- 将不确定度、冲突、缺失和过期数据纳入风险。
- 输出 0–100 分数、等级、证据、解释和建议约束。
- 支持规则、统计、学习模型和混合插件，提供安全基线回退。
- 对阈值跨越和风险突变立即发布事件。

不负责生成路径、选择最终策略或发控制命令。

## 3. 输入

| 输入 | 要求 |
|---|---|
| `twin.snapshot` | revision、watermark、staleness、frame、地图/配置版本 |
| `twin.prediction` | horizon、step、模型、协方差 |
| `TargetMessage` | 动态目标、分类概率、状态和不确定度 |
| `EnvironmentMessage` | 时空覆盖、有效期、risk_factors |
| Vehicle/Energy State | 电量、预计续航、动力/载荷包线 |
| Mission/Path | 任务阶段、目标、候选/当前路径 |
| Link/Health | 控制链路、传感器、服务、飞控健康 |
| 风险策略配置 | 等级阈值、权重、硬规则、飞行器限制 |

所有输入需绑定同一或可兼容的 Twin revision。过期或冲突输入不能被当作低风险。

## 4. 输出

- `risk.update`：符合 [Risk Message](../../03_INTERFACE_SPEC.md#7-risk-message)。
- `risk.threshold_crossed`：等级跨越、进入/退出 critical。
- `risk.constraint`：结构化禁入体积、速度/高度/任务限制。
- `health.update`：评估器延迟、输入覆盖、模型和回退状态。

## 5. 内部结构与风险模型

### 5.1 维度

| 维度 | 典型特征 | 典型硬条件 |
|---|---|---|
| 天气 | 风/阵风、侧风、降水、能见度、结冰、对流、未知覆盖 | 超出飞行器天气包线 |
| 碰撞 | CPA/TCPA、保护区侵入概率、相对速度、目标意图、静态净空 | 预测保护体重叠 |
| 能源 | SOC、剩余航程、返航/备降需求、温度、功耗不确定度 | 无法安全抵达备降点 |
| 通信 | RSSI/延迟/丢包、控制 Ack、链路冗余、盲区预测 | 控制链路持续丢失 |
| 系统 | 传感器覆盖、时钟、插件、CPU/GPU、飞控/定位健康 | 关键状态过期或安全组件失效 |

### 5.2 评分与等级

每个维度先输出 `score_i ∈ [0,100]` 和 uncertainty。综合基线：

```text
base = weighted_max_or_sum(score_i, mission_phase)
uncertainty_penalty = f(missing, stale, conflict, prediction_covariance)
score = clamp(max(hard_rule_floor, base + uncertainty_penalty), 0, 100)
```

默认等级：

| 分数 | 等级 |
|---:|---|
| 0–24 | `LOW` |
| 25–49 | `MODERATE` |
| 50–74 | `HIGH` |
| 75–100 | `CRITICAL` |
| 无法给出可信上界 | `UNKNOWN`，决策按至少 HIGH 处理 |

阈值可按飞行器/任务配置，但放宽需安全审批。硬规则能将风险下限提升，不能被加权平均稀释。

### 5.3 碰撞风险

对每个目标和静态障碍：

1. 用 Twin Prediction 获取自机/目标位置分布；
2. 计算确定性 CPA/TCPA 作为特征；
3. 基于协方差或 Monte Carlo/解析近似估计保护体侵入概率；
4. 考虑反应时间、控制延迟和制动/转弯包线；
5. 取时间 horizon 内最坏点并保留目标贡献排名。

基线保护距离按车辆类型、相对速度和定位不确定度动态扩大，不使用固定单一半径覆盖全部场景。

### 5.4 天气风险

沿当前/候选路径采样 Environment 场，与飞行器天气包线比较。空间无覆盖、有效期不足或冲突会增加 uncertainty penalty。

### 5.5 能源风险

```text
required_energy =
  energy_to_goal_or_safe_site
  + wind/weather correction
  + maneuver reserve
  + configured contingency reserve
```

若预测剩余能源分布的保守分位数低于 required，则至少 HIGH；无法抵达任何安全点为 CRITICAL。

## 6. 解释结构

每条 explanation 包含：

- 稳定 `code`；
- severity；
- 面向操作员的短说明；
- 证据值、单位、来源 event ID/Twin entity；
- 触发阈值和策略版本；
- 可选建议动作/约束。

示例 code：`CLOSING_TRACK`、`WIND_ENVELOPE_EXCEEDED`、`ENERGY_RESERVE_LOW`、`CONTROL_LINK_STALE`、`PERCEPTION_COVERAGE_LOST`、`ENVIRONMENT_UNKNOWN`。

解释必须由结构化事实生成，不允许模型只返回无法审计的自由文本。

## 7. 插件接口

```python
class RiskFactorPlugin(Plugin, Protocol):
    @property
    def dimension(self) -> RiskDimension: ...
    async def evaluate(self, context: RiskContext) -> RiskFactorResult: ...

class RiskAggregatorPlugin(RiskPlugin, Protocol):
    async def aggregate(
        self,
        factors: Sequence[RiskFactorResult],
        policy: RiskPolicy
    ) -> RiskMessage: ...
```

插件必须声明 horizon、必需输入、新鲜度、计算 deadline 和解释 code。学习模型不能绕过硬规则；模型失败回退规则基线。

## 8. 缓存与触发

- Twin revision、环境更新、目标威胁变化、任务/路径、健康等级变化均触发评估。
- 周期刷新避免长时间无事件造成过期，默认 5 Hz（按部署调优）。
- 可缓存与 `(twin_revision, policy_hash, plugin_versions)` 完全匹配的结果。
- 新 revision 到达时取消未提交的旧评估；已发布结果不可修改，只能发布新 `risk_id`。

## 9. 错误与降级

| 情况 | 行为 |
|---|---|
| 部分 factor 插件超时 | 使用该维度保守上界并标记 degraded |
| Twin/vehicle state 过期 | `UNKNOWN`/HIGH 起步，限制新路径授权 |
| 学习模型不可用 | 切换规则基线 |
| 策略配置无效 | readiness=false，禁止 LIVE |
| 输出 Schema 非法 | 不发布，回退基线并告警 |
| 评估超 deadline | 发布超时健康事件；Decision 使用安全策略 |

## 10. 配置示例

```yaml
risk:
  refresh_hz: 5
  horizon_s: 15
  levels: {moderate: 25, high: 50, critical: 75}
  uncertainty:
    stale_input_penalty: 20
    unknown_environment_floor: 55
  collision:
    min_horizontal_separation_m: 15
    min_vertical_separation_m: 8
    reaction_time_s: 1.0
  energy:
    contingency_reserve_pct: 20
  plugins:
    aggregator: safety_weighted_max@1.0.0
```

## 11. 外部依赖

Twin Query/Prediction、EventBus、Clock、配置/插件仓库、数值/几何库和 Metrics。Risk 模块不直接读 Perception/Weather 私有数据库。

## 12. 验收标准

1. 标准场景中所有定义的 imminent collision 在决策 deadline 前达到 HIGH/CRITICAL，漏报为 0。
2. 无威胁标称场景误报 HIGH/CRITICAL 率 ≤ 项目基准 2%。
3. P95 评估延迟 ≤ 35 ms（典型 100 动态目标、15 s horizon）。
4. 每条 Risk 都有 `twin_revision,valid_until,dimensions,explanations`。
5. 缺失/过期关键输入不输出 LOW。
6. 学习插件崩溃后规则基线在 500 ms 内接管。
7. 阈值边界和硬规则结果具确定性。

## 13. 测试方法

- 各维度公式、阈值、单位、边界和硬规则单元测试。
- CPA/TCPA、协方差传播和保护体概率的解析/Monte Carlo 对照。
- 参数化场景：交叉、对向、追尾、静态障碍、风场、低能量、断链、多故障。
- 不确定度单调性：其他条件相同，协方差/缺失增加不应降低风险。
- 决策回放：与人工标注/安全规则 golden result 对比。
- 性能测试目标数×horizon×step 矩阵，并验证取消旧评估。
