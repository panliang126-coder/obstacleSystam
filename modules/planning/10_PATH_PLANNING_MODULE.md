# 路径规划模块

## 1. 模块目标

在地图、飞行器动力学、任务、天气、动态障碍和风险约束下，生成可执行、可验证、带有效期和代价解释的全局/局部路径，支持动态重规划和算法插件替换。

## 2. 模块职责

- 全局规划：任务起终点、航路/地理围栏和静态障碍。
- 局部避障：短 horizon 动态目标、天气和飞行器实时状态。
- 动态重规划：路径失效、风险变化、任务/能源/通信变化。
- 候选路径排序、硬约束验证和输出。
- 管理 A*、Dijkstra、RRT*、MPC、RL 等插件。
- 提供计算 deadline、取消、回退和路径平滑。

不负责选择 Continue/Return/Land 等最终策略，也不把路径直接发送飞控。

## 3. 输入

| 输入 | 说明 |
|---|---|
| Plan Request | mission、start/goal、horizon、deadline、约束 |
| Twin Snapshot/Prediction | 指定 revision 的车辆、目标、地图、环境 |
| `RiskMessage` | 风险因子、禁入体积、有效期 |
| Vehicle Envelope | 速度、加速度、爬升、转弯率、制动距离 |
| Geofence/Corridor | hard/soft 空域约束及有效期 |
| Current Path/Execution | 便于局部修补和切换连续性 |
| Planner 配置/插件 | 算法、代价权重、预算、回退链 |

输入 Risk 或 Twin 过期时不能生成可授权的 `SELECTED` 路径。

## 4. 输出

- `path.proposed`：一个或多个候选 [Path Message](../../03_INTERFACE_SPEC.md#8-path-message)。
- `path.selected`：规划器内部选出的最佳可行候选，仍需 Decision/Safety Gate。
- `path.failed`：结构化失败原因、已尝试插件和约束冲突。
- `planner.progress`：仅 GUI/诊断使用，不进入控制。
- `health.update`：deadline、不可行率、回退和资源。

## 5. 内部结构与规划层级

```text
Mission Goal + Static Map
          |
   Global Planner (seconds/minutes horizon)
          |
  Global Corridor / Waypoints
          |
 Dynamic Twin + Risk Constraints
          |
 Local Planner (0.5–20 seconds horizon)
          |
 Trajectory Validator + Smoother
          |
      PathMessage
```

- 全局路径可低频更新；局部路径必须连续衔接当前执行状态。
- Path 中航点仅是交换格式；内部可使用时间参数化轨迹，输出必须保留时间/速度约束。

## 6. 统一规划请求

```python
@dataclass(frozen=True)
class PlanRequest:
    request_id: UUID
    mission_id: str
    vehicle_id: str
    kind: Literal["GLOBAL", "LOCAL", "REPLAN", "EMERGENCY"]
    twin_revision: int
    risk_id: UUID
    start: KinematicState
    goal: GoalRegion
    current_path: PathMessage | None
    hard_constraints: tuple[Constraint, ...]
    soft_constraints: tuple[CostTerm, ...]
    deadline: datetime
```

约束具有稳定类型、单位、来源和有效期。

## 7. 插件接口与能力

```python
class PlannerPlugin(Plugin, Protocol):
    def supports(self, request: PlanRequest) -> SupportResult: ...
    async def plan(self, request: PlanRequest, cancel: CancelToken) -> PathMessage: ...
```

| 算法 | 适合 | 限制/门控 |
|---|---|---|
| Dijkstra | 小型离散图、最优基线 | 大图慢，不直接处理动力学 |
| A* | 栅格/航路全局规划 | heuristic 必须可审计；需后处理 |
| RRT* | 3D 连续复杂空间 | 结果和时间受采样影响；种子必须记录 |
| MPC | 局部动态避障、动力学约束 | 依赖模型和求解器 deadline |
| RL | 复杂策略候选/启发 | 只能在硬约束验证后使用；需 OOD/回退 |

插件 manifest 声明 `GLOBAL/LOCAL`、2D/3D、动态障碍、动力学、确定性、最大问题规模和资源。

## 8. 约束与代价

### 8.1 硬约束

- 地形/建筑/静态障碍净空；
- 动态保护体；
- 地理围栏和空域时间窗；
- 飞行器速度、加速度、爬升/下降和转弯包线；
- 最低能源/备降可达；
- 禁止进入未知且政策设为 hard-block 的环境区域；
- 路径连续性和有效时间。

任何 hard constraint 失败，`validation` 不能为 true。

### 8.2 软代价

```text
J = w_distance*distance
  + w_time*time
  + w_energy*energy
  + w_risk*integrated_risk
  + w_smooth*smoothness
  + w_deviation*deviation_from_mission
```

原始量先按配置基准归一化，Path 输出分项与权重/策略哈希，避免只给一个不可解释 total。

## 9. 动态重规划触发

- 当前 Path 即将过期；
- Risk 等级/约束变化；
- 预测动态目标侵入 corridor；
- 环境天气覆盖/风险变化；
- Vehicle 状态偏离轨迹；
- 能源/通信/系统健康变化；
- 任务更新或 Control Ack 拒绝。

去抖与滞回防止频繁抖动，但 CRITICAL 事件绕过去抖立即触发。新 request 取消旧的未提交局部规划。

## 10. 路径验证

独立 `TrajectoryValidator` 不属于具体 Planner：

1. Schema、frame、时间单调、至少两个航点；
2. 起点与当前状态在容差内；
3. 连续碰撞检测，不只检查离散航点；
4. 动力学和控制采样可实现；
5. geofence/terrain/天气/风险硬约束；
6. Path 的 twin/risk revision 和有效期；
7. 切换段连续性和最小净空；
8. 紧急停止/备选行为可用。

学习规划器输出必须经过同一 validator，无例外。

## 11. 回退链

示例：

```text
local MPC
  -> timeout/infeasible: bounded RRT* using remaining deadline
  -> still infeasible: validated braking/hold trajectory
  -> hold unsafe: Decision requested RETURN/LAND/ABORT
```

回退路径也必须验证。Planner 只报告不可行，不自行改变任务策略。

## 12. 配置示例

```yaml
planning:
  global:
    plugin: astar_3d@1.0.0
    resolution_m: 5
    deadline_ms: 500
  local:
    plugin: local_mpc@1.1.0
    horizon_s: 8
    step_s: 0.2
    deadline_ms: 90
    fallback: rrt_star_bounded@1.0.0
  validation:
    interpolation_step_m: 0.5
    minimum_clearance_m: 10
  costs:
    distance: 1.0
    time: 1.0
    energy: 1.5
    risk: 4.0
```

## 13. 错误与降级

| 情况 | 行为 |
|---|---|
| Planner timeout | 取消、尝试回退；不发布半成品为 valid |
| 问题不可行 | 发布 `path.failed`，列出冲突约束 |
| Twin/Risk 过期 | 取消并请求最新评估 |
| 地图缺失 | readiness=false；禁止 LIVE |
| 插件崩溃 | 隔离、回退已验证插件 |
| Validator 异常 | 路径一律拒绝，不能 fail-open |

## 14. 外部依赖

Twin Query/Prediction、Risk、地图/几何、求解器、EventBus、Clock、Plugin/Config Repository、Metrics。求解器许可证和线程配置纳入部署清单。

## 15. 验收标准

| 指标 | 基线 |
|---|---:|
| 标准可行场景路径成功率 | ≥ 99% |
| 硬约束违规 | 0 |
| 静态全局规划 P95 | ≤ 500 ms（定义地图规模） |
| 局部重规划 P95 | ≤ 100 ms（典型 100 目标） |
| 最小净空 | 不低于场景/车辆配置 |
| 相同确定性输入/插件输出 | 语义一致 |
| 不可行结果含结构化原因 | 100% |
| RL/学习输出未经 Validator 进入 Decision | 0 |

## 16. 测试方法

- 几何、代价、约束、插值、连续碰撞和动力学单元测试。
- 空地图、窄通道、3D 障碍、移动交叉、无解、起点冲突、目标移动等场景。
- property-based：所有 valid Path 满足硬约束不变量。
- 与 Dijkstra/A* 小问题最优基线对照；RRT* 固定种子回归。
- deadline/cancel、求解器失败、地图版本切换和消息风暴。
- 端到端模拟闭环测试路径切换连续性、碰撞数和控制 Ack。
