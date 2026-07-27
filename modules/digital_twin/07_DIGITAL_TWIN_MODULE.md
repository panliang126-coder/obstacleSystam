# 数字孪生模块

## 1. 模块目标

维护与真实/模拟低空场景一致的、带版本和不确定度的统一世界状态，为风险评估、路径规划、决策、可视化和仿真推演提供同一事实视图。

## 2. 模块职责

- 管理飞行器、动态目标、静态障碍、天气、地图、任务和系统健康实体。
- 按事件更新状态，生成原子 `TwinSnapshot` 和单调 revision。
- 维护实体生命周期、来源证据、时空索引和数据新鲜度。
- 对动态实体进行短时状态预测，并显式传播不确定度。
- 创建只读分支执行 what-if 推演，不污染实时主世界。
- 支持检查点、事件重建和历史回放。

不负责原始检测、最终风险评分或路径选择。

## 3. 输入

| 输入 | 用途 |
|---|---|
| `perception.tracks` | 动态目标状态/生命周期 |
| `environment.update` | 环境场和风险因子 |
| `vehicle.state` | 自机/机队状态 |
| Static Map/Geofence | 建筑、地形、禁飞区、航路 |
| Mission State | 目标、约束、阶段 |
| `health.update` | 传感器/服务/链路状态 |
| Control Ack | 计划执行状态和现实反馈 |

每次更新必须通过 Schema、时间、frame、revision 和来源检查。

Phase 2 在感知模块实现前允许订阅 `sensor.normalized.*`，但仅将其投影为
Sensor Evidence/Health 实体，用于验证 Driver→Bus→Twin 基础链路。该投影不得
把雷达检测或模拟 truth 冒充为系统航迹；Phase 3 起动态目标仍只由
`perception.tracks` 更新。

## 4. 输出

- `twin.snapshot/1.0`：轻量快照元数据、revision、关键实体及大型状态引用。
- `twin.entity.changed/1.0`：增量更新，供 GUI/查询投影使用。
- `twin.prediction/1.0`：指定 horizon 的预测状态和置信区间。
- `twin.branch.result/1.0`：仿真分支结果，带父 revision/假设/插件版本。
- Query API：按 ID、空间体积、时间/revision 查询。

输出事件继承输入 `trace_id`；多输入聚合时产生新 trace 并在 `input_refs` 中列出所有因果事件。

## 5. 内部结构与实体模型

```text
World
├── vehicles[vehicle_id]
│   ├── kinematics / energy / flight_mode
│   ├── mission / active_path / decision
│   └── sensors / links / health
├── tracks[track_id]
│   ├── class probabilities
│   ├── pose / velocity / covariance
│   └── lifecycle / provenance
├── environment
│   ├── local fields / grids / validity
│   └── weather risk factors
├── static_world
│   ├── terrain / buildings / obstacles
│   └── geofences / corridors
└── metadata
    ├── revision / observed_at / watermark
    └── map/config/schema versions
```

实体字段包含 `entity_id, entity_type, revision, valid_from, valid_to, state, quality, source_refs`。

## 6. 状态更新

### 6.1 单写者原则

每个 Twin shard/vehicle 的权威 revision 由一个逻辑单写者提交。多个消费者可读取不可变快照。扩展时按 region/vehicle 分片，但跨分片查询必须注明一致性水位线。

### 6.2 更新流程

```text
Event -> Validate -> Deduplicate -> Resolve entity
      -> Check source/revision/time -> Apply reducer
      -> Update spatial index -> Commit revision + outbox
      -> Publish snapshot/entity.changed
```

Reducer 是纯函数：`new_state = reduce(old_state, event, context)`。禁止 Reducer 读取墙钟、网络或随机源。

### 6.3 冲突

- 同一实体旧 revision：忽略并计数。
- 不同源冲突：按配置的源可信度和不确定度融合；保留冲突标记。
- 地图/配置版本不一致：拒绝更新或创建隔离 branch，不能混合。
- 晚到事件：可更新历史投影，但不倒退实时 revision。

## 7. State Snapshot

Snapshot 至少包含：

```json
{
  "twin_id": "site-alpha/uav-001",
  "revision": 18722,
  "as_of": "2026-07-27T03:20:15.220Z",
  "watermark": "2026-07-27T03:20:15.120Z",
  "frame_id": "site-alpha-enu-v1",
  "map_version": "site-alpha-map@3.2.0",
  "config_hash": "sha256:...",
  "vehicle_ref": "state://vehicle/uav-001/18722",
  "sensor_refs": ["state://sensor/radar-front-01/18722"],
  "track_refs": ["state://track/..."],
  "environment_ref": "object://weather-grid/...",
  "staleness": {"vehicle_ms": 20, "tracks_max_ms": 85, "environment_ms": 220},
  "quality": {"valid": true, "confidence": 0.88, "flags": []}
}
```

Risk、Path 和 Decision 必须记录使用的 revision，不能只引用“最新状态”。

当前 Phase 2 内存实现提供单写者 revision、事件去重、晚到保护、实体容量上限
和确定性快照哈希。持久化 checkpoint、空间索引、预测和 what-if branch 在后续
里程碑实现，不能将当前内存 Store 用作 LIVE 权威状态。

## 8. 状态预测

预测插件接口：

```python
class StatePredictorPlugin(Plugin, Protocol):
    async def predict(
        self,
        snapshot: TwinSnapshot,
        horizon_s: float,
        step_s: float,
        assumptions: PredictionAssumptions
    ) -> TwinPrediction: ...
```

- 基线目标模型：恒速/恒加速度并传播协方差。
- 飞行器模型：动力学包线和当前 path/setpoint。
- 天气：在有效 horizon 内平流/插件 nowcast；超出后 uncertainty 增大。
- 预测永远携带 horizon、step、模型版本和假设。

## 9. 仿真推演分支

Branch 创建参数：`parent_revision, proposed_path/decision, horizon, seed, model_versions`。

规则：

- 分支使用 copy-on-write/不可变快照。
- 分支事件 Topic 与实时 Topic 隔离，含 `branch_id`。
- 分支无 Control Gateway 权限。
- 有 deadline；超时返回部分结果和不确定性，不能静默采用。
- 结果包含最小间隔、风险轨迹、能源变化、约束违例和运行耗时。

## 10. 时空索引

- 动态目标：内存 R-tree/k-d tree 或等价索引，按 revision 原子切换。
- 静态地图：瓦片/BVH/占据栅格，版本不可变。
- 天气：规则网格/八叉树，存 coverage 与时间层。
- Query 必须指定 `as_of/revision` 和 frame；默认查询的“latest”仅用于 GUI，不用于安全决策。

## 11. 检查点与恢复

- 每 N revisions/时间保存快照，事件日志为事实源。
- 恢复：加载最近兼容 checkpoint → 校验哈希 → 重放后续事件 → 验证最终 revision。
- checkpoint 包含 map/config/schema/plugin manifest。
- 不兼容快照需要 upcaster；禁止忽略版本直接反序列化。

## 12. 错误与降级

| 情况 | 行为 |
|---|---|
| Entity update 非法 | 隔离，保持旧状态，发布健康事件 |
| 输入过期 | 保留最后状态并增加 staleness/协方差 |
| 空间索引失败 | 不提交 revision；回滚事务 |
| 持久化暂时不可用 | 有界 WAL，继续内存状态；耗尽前告警并降级 |
| 地图缺失/损坏 | readiness=false；规划不可进入 LIVE |
| 预测插件失败 | 回退运动学基线或标记 prediction unavailable |

## 13. 外部依赖

EventBus、Clock、TwinRepository、对象存储、地图/几何库和 predictor 插件。渲染引擎不是 Twin 领域模型依赖，GUI 通过 Query/事件获取数据。

## 14. 验收标准

| 指标 | 基线 |
|---|---:|
| 单实体更新 P95 | ≤ 10 ms |
| 典型快照生成 P95 | ≤ 20 ms |
| revision 倒退/重复提交 | 0 |
| 快照与事件因果可追溯 | 100% |
| 1000 动态实体、10 Hz 更新 | 无无界内存增长，满足部署预算 |
| 5 s 运动学预测位置误差 | 项目场景 RMSE ≤ 3 m |
| checkpoint 恢复状态哈希 | 与连续运行一致 |
| branch 污染实时状态 | 0 |

## 15. 测试方法

- Reducer 纯函数/property-based 测试。
- 重复、乱序、冲突、晚到和 revision CAS 并发测试。
- 坐标和时空范围查询基准测试。
- 崩溃点注入：提交前、数据库提交后/事件发布前、checkpoint 中途。
- 固定事件流从空状态重建并比较快照哈希。
- 运行多个 branch，确认种子复现和主世界隔离。
