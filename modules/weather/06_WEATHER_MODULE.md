# 天气环境感知模块

## 1. 模块目标

融合气象雷达、风场、温度、湿度、降水、能见度及可选外部天气源，形成具有时空范围、置信度、有效期和来源证据的 `EnvironmentMessage`，为数字孪生、风险和规划提供环境约束。

## 2. 模块职责

- 解读标准化气象观测，执行范围、物理一致性和异常值质量控制。
- 对不同时间、分辨率和覆盖范围的数据进行同化/插值。
- 估计风矢量、阵风、降水、能见度、温湿度及相关不确定度。
- 计算标准化环境风险因子，而不直接做最终飞行决策。
- 管理天气网格、有效时间、预报 horizon 和数据新鲜度。
- 支持真实气象设备、模拟器和外部服务适配器无缝替换。

## 3. 输入

| 输入 | 说明 |
|---|---|
| `sensor.normalized.weather` | 气象雷达、微型气象站、风速仪、雨量计等 |
| Vehicle State | 机载测量的位置、姿态和运动补偿 |
| Terrain/Map | 高程、建筑物和粗糙度等空间背景 |
| External Weather Adapter | 外部预报/nowcast，必须标识许可、时间和分辨率 |
| Scenario Weather | SIM/REPLAY 场景天气，与真实接口一致 |
| 配置 | 质量阈值、融合权重、网格、飞行器天气限制 |

## 4. 输出

- `environment.update`：符合 [Environment Message](../../03_INTERFACE_SPEC.md#5-environment-message)。
- `weather.alert`：结构化突变/阈值事件，如阵风、强对流、能见度骤降。
- `health.update`：观测覆盖、年龄、冲突、模型和数据源健康。
- 可选大型栅格写入对象存储，消息用 `grid_ref` 引用。

## 5. 内部结构

```text
Weather Observations / External Forecast / Simulation
                        |
              Source-specific Adapter
                        |
               Quality Control (QC)
                        |
           Time Align + Spatial Regrid
                        |
            Fusion / Assimilation Plugin
                        |
        Derived Variables + Risk Factors
                        |
             EnvironmentMessage + Grid
```

### 5.1 质量控制

- 物理范围：湿度 0–100%、能见度 ≥ 0、压力/温度在设备允许范围。
- 变化率：超过可配置物理上限标记 spike，不自动删除原始观测。
- 空间一致性：邻近站/网格差异异常时输出 conflict。
- 设备自检：加热、结冰、遮挡、标定和通信状态进入质量权重。
- 运动补偿：机载风测量必须去除机体速度和姿态影响。

### 5.2 时空融合

- 所有数据映射到统一 `WeatherGridSpec`，包含 CRS/frame、原点、分辨率、层高和时间步。
- 插值不得超出数据源声明的最大距离/时间；超出区域为 unknown，而不是零风险。
- 融合输出保存每个源的权重、残差和 coverage。
- 外部预报与本地 nowcast 分开记录，不用未来预报覆盖已发生观测。

### 5.3 风险因子

输出 0–1 归一化因子：

- `wind`：持续风、侧风、垂直风、阵风；
- `precipitation`：雨/雪/冰雹及雷达强度；
- `visibility`：雾、低云、烟尘；
- `icing`：温湿度和液态水条件；
- `convective`：强对流/雷电/快速垂直气流；
- `uncertainty`：数据缺失、冲突和外推。

因子是中间量，最终 0–100 风险由 Risk 模块结合飞行器包线和任务上下文计算。

## 6. 插件接口

```python
class WeatherQcPlugin(Plugin, Protocol):
    async def validate(self, observations: Sequence[SensorMessage]) -> QcBatch: ...

class WeatherFusionPlugin(WeatherPlugin, Protocol):
    async def estimate(
        self,
        observations: QcBatch,
        grid: WeatherGridSpec,
        horizon_s: float
    ) -> EnvironmentMessage: ...
```

插件清单声明变量、空间维度（2D/3D）、最大 horizon、分辨率、所需源和不确定度输出能力。

## 7. 数据新鲜度与状态

每个变量独立记录 `observed_at`、`valid_to` 和 confidence。环境快照状态：

- `VALID`：覆盖和年龄满足规划需求；
- `PARTIAL`：部分变量/区域未知；
- `STALE`：超出时限，只可作为历史参考；
- `CONFLICTED`：源之间显著冲突；
- `UNAVAILABLE`：没有可用环境数据。

Risk 模块必须把后三种状态转换为保守风险，而非 `LOW`。

## 8. 配置示例

```yaml
weather:
  grid:
    frame_id: site-alpha-enu-v1
    horizontal_resolution_m: 25
    vertical_resolution_m: 10
    horizon_s: 120
  freshness_s:
    local_wind: 5
    visibility: 10
    external_forecast: 300
  qc:
    max_wind_m_s: 60
    max_temperature_change_deg_c_min: 8
  plugins:
    fusion: local_weather_fusion@1.0.0
```

## 9. 错误与降级

| 情况 | 行为 |
|---|---|
| 本地气象源离线 | 使用仍有效外部/历史模型，增加 uncertainty |
| 外部服务不可用 | 不影响本地链；停止扩大预报 horizon |
| 源冲突 | 保留冲突，采用风险上界或配置的保守融合 |
| 网格生成超时 | 发布最新有效低分辨率结果并标记降级 |
| 区域无观测 | 明确 unknown，规划按禁止或高代价区处理 |
| 气象突变 | 立即发布 alert，触发 Risk/Planner，不等待周期更新 |

## 10. 外部依赖

数值/栅格库、对象存储、地图地形、EventBus、Clock、Metrics、Artifact Repository。外部 API 只能在 Adapter 层使用，测试通过录制 fixture 或模拟服务完成。

## 11. 验收标准

| 指标 | 基线 |
|---|---:|
| 本地风速 RMSE | ≤ 2.0 m/s（项目验证场） |
| 风向 MAE | ≤ 15°（风速大于最低有效阈值时） |
| 温度 MAE | ≤ 1.5 °C |
| 降雨有/无识别召回 | ≥ 0.90 |
| Environment 输出 P95 | ≤ 100 ms（本地更新） |
| 覆盖、有效期、confidence、provenance 完整率 | 100% |
| 未知区域错误标记为低风险 | 0 |
| 突变告警触发 | 观测后 ≤ 500 ms |

## 12. 测试方法

- QC 边界、单位、异常变化率、重复/乱序/过期测试。
- 合成均匀风、剪切风、阵风、雨带、雾区的解析场误差测试。
- 站点缺失、雷达遮挡、外部服务断连和冲突源故障注入。
- 不同网格分辨率、覆盖和 horizon 的性能/内存测试。
- 对相同 Scenario/seed 执行确定性回放并比较网格哈希和风险因子。
- 与独立参考站/留出数据做交叉验证，报告空间分层误差。

## 13. Phase 3 可执行基线

当前 `local_weather_weighted_fusion@1.0.0` 插件提供：

- 气象 `sensor/1.0` 的风、阵风、温湿度、降水、能见度和气压物理 QC；
- 按输入质量 confidence 加权，阵风/降水/能见度采用保守聚合；
- 有效期、BOX3D coverage、来源权重、风不确定度和六类风险因子；
- 乱序、过期、非法和无观测的显式降级；
- 无观测时 `quality.valid=false`、confidence=0 且
  `risk_factors.uncertainty=1.0`，不会把未知天气标记为低风险；
- 确定性模拟天气观测和端到端 Twin 更新。

合成基线场景要求风速误差 ≤ 2 m/s、本地输出 P95 ≤ 100 ms、provenance 与
coverage 完整率 100%。尚未实现天气网格、空间插值、外部预报、突变告警、
多站冲突检测和独立参考站交叉验证。
