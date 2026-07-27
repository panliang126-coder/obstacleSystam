# ADR-0003：Phase 3 可审计感知与天气基线

- 状态：Accepted
- 日期：2026-07-27
- 决策者：项目 Phase 3 基线

## 背景

Phase 3 需要在不引入大型模型或真实设备的情况下打通感知、天气和 Twin，并为
后续算法提供可量化、可回放的基准。模拟器 truth 若进入业务观测会使精度测试失真；
天气缺测若输出零风险会形成 fail-open。

## 决策

1. 雷达模拟业务载荷只包含量测，不包含 truth target ID/类别。
2. 首版感知采用 ENU 转换、恒速预测、距离硬门控和最近邻关联；输出完整航迹状态、
   协方差和 source refs。分类在没有 EO/IR 证据时固定为 `UNKNOWN`。
3. 重复/乱序雷达输入不得更新航迹；丢帧按配置进入 COASTING/LOST。
4. 首版天气插件执行物理 QC 和 confidence 加权融合，对阵风、降水和能见度采用
   保守聚合。
5. 天气没有可用观测时输出无效、零 confidence 和 uncertainty=1，不把未知变量
   伪造成安全值。
6. 插件通过既有 `PluginContext`、正式消息 Schema 和 Event Bus 工作，Twin 只消费
   `perception.tracks` 与 `environment.update`。

## 备选方案

### 用模拟 truth ID 直接形成航迹

拒绝。虽然容易得到零 ID switch，但违反 truth 隔离，无法代表真实感知能力。

### 无天气时沿用最后值且保持 valid

拒绝。会无限期把过期数据冒充实时数据，风险模块无法安全降级。

### 立即引入深度模型和完整滤波库

暂不采用。Phase 3 先建立契约、状态机、数据集指标和回放基线；模型 Runtime 和
artifact 审批在后续以插件替换，不改变跨模块消息。

## 兼容影响

不改变 `sensor/1.0`、`target/1.0` 或 `environment/1.0` 字段。移除的是模拟器
未进入正式 Schema 的 truth 提示字段，因此不存在已发布契约兼容问题。

## 迁移与回滚

后续 Tracker/Fusion/Weather 插件必须通过相同契约、降级和场景测试后 shadow
上线。回滚只需恢复插件 manifest 选择，历史输出保留插件版本以便审计。
