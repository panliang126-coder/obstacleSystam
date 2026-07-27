# 多传感器感知模块

## 1. 模块目标

把已校验、已时间同步、坐标语义明确的雷达、EO、IR、声学、电磁等观测转换为可追踪、带不确定度和来源证据的 `TargetMessage`。模块支持检测、跟踪、分类、Embedding 和多传感器融合，并允许算法插件动态替换。

## 2. 模块职责

- 按传感器类型完成预处理、检测、特征提取和单源跟踪。
- 在统一时间窗口和 ENU 坐标系内执行数据关联与航迹融合。
- 输出位置、速度、协方差、类别概率、航迹状态和源事件引用。
- 管理模型批处理、设备选择、超时、影子模型和降级模型。
- 检测数据冲突、模型漂移和输入过期，发布健康/质量指标。

不负责：

- 设备字节解码、标定文件采集和 NED/ENU 设备转换（Driver/Normalizer 负责）；
- 天气估计、风险判断和路径规划；
- 直接生成控制命令。

## 3. 输入

| 输入 | Schema/Topic | 要求 |
|---|---|---|
| 雷达扫描/检测 | `sensor/1.0` / `sensor.normalized.radar` | 完整扫描、frame、标定、SNR |
| EO 图像引用 | `sensor/1.0` / `sensor.normalized.eo` | 时间、内外参、图像 URI |
| IR 图像引用 | `sensor/1.0` / `sensor.normalized.ir` | 温标/标定信息 |
| 声学/电磁特征 | `sensor/1.0` | 频谱、阵列几何或方位估计 |
| Vehicle State | `vehicle.state/1.0` | 对应时刻姿态/位置 |
| Frame Transform | Transform API | 在观测时刻有效 |
| 感知配置/模型 | 配置与 Artifact Repository | 哈希校验、兼容版本 |

输入若 `quality.valid=false`，默认不参与融合但记录指标。低置信数据可进入插件，由插件在输出协方差中体现。

## 4. 输出

| 输出 | Topic | 说明 |
|---|---|---|
| 检测批次 | `perception.targets` | 可无系统 track_id |
| 融合航迹批次 | `perception.tracks` | 进入 Twin 的正式输出 |
| 插件/模型健康 | `health.update` | 延迟、错误、设备、模型状态 |
| 感知指标 | Metrics | precision proxy、队列、FPS、age、track count |
| 隔离记录 | `deadletter.perception` | 非法输出或不可转换数据 |

正式输出必须符合 [Target Message](../../03_INTERFACE_SPEC.md#6-target-message)。

## 5. 内部结构

```text
Sensor subscriptions
        |
Input Gate (schema/freshness/calibration/frame)
        |
Time Window Buffer ---- Vehicle State/Transform Cache
        |
 +------+------------------+
 |                         |
Radar Pipeline      EO/IR/Other Pipelines
 | detect/track      | preprocess/detect/embed
 +----------+--------+
            |
     Association Engine
            |
       Track Filter
            |
 Classification Fusion
            |
 Track Lifecycle Manager
            |
 TargetMessage Validator
```

### 5.1 Input Gate

- 验证 Schema major、sensor_id allowlist、时间年龄、frame 和 calibration。
- 对重复 `event_id` 幂等忽略。
- 按传感器配置决定过期阈值；拒绝原因必须结构化计数。

### 5.2 单源处理

- 雷达：杂波过滤、检测聚类、量测噪声建模、单源航迹。
- EO/IR：解码/缩放、检测、可选分割、Embedding、2D/3D 投影。
- 声学/电磁：方位/类别特征；位置不可观时输出观测射线和高不确定度，不伪造 3D 坐标。

### 5.3 数据关联与融合

基线实现可采用门控 + Hungarian/JPDA 等可插拔方法：

1. 将量测转换到同一 `frame_id` 和融合时刻；
2. 用 Mahalanobis/IoU/Embedding/类别一致性计算关联代价；
3. 先做硬门控，再优化匹配；
4. 使用 Kalman/EKF/UKF 或插件化滤波更新状态和协方差；
5. 未匹配量测产生 tentative track；未更新航迹进入 coasting；
6. 输出关联证据和冲突标记。

类别融合保留完整概率分布。多个传感器并不默认独立，避免重复证据导致过度自信。

### 5.4 航迹状态机

```text
NEW -> TENTATIVE -> CONFIRMED -> COASTING -> LOST -> DELETED
          |              ^          |
          +--------------+----------+
```

- `TENTATIVE -> CONFIRMED`：默认最近 M=5 帧中命中 N=3，可配置。
- `CONFIRMED -> COASTING`：未匹配但仍在预测容忍窗口。
- `COASTING -> LOST`：超过目标类别/速度相关 timeout。
- `LOST` 必须至少发布一次 tombstone 后再内部删除。
- ID 不能因单帧分类改变而重建；重关联需记录 lineage。

## 6. 插件接口

除基础 `PerceptionPlugin` 外，细分端口：

```python
class DetectorPlugin(Plugin, Protocol):
    async def detect(self, sample: SensorMessage) -> DetectionBatch: ...

class TrackerPlugin(Plugin, Protocol):
    async def update(self, detections: DetectionBatch, state: TrackSet) -> TrackSet: ...

class FusionPlugin(Plugin, Protocol):
    async def fuse(self, window: FusionWindow, prior: TrackSet) -> TargetMessage: ...

class EmbedderPlugin(Plugin, Protocol):
    async def encode(self, object_crops: Sequence[ObjectCrop]) -> EmbeddingBatch: ...
```

插件清单必须声明传感器类型、frame 能力、最大 batch、deadline、模型 artifact、硬件和输入/输出 Schema 范围。

## 7. 模型与资源管理

- 模型加载前校验 SHA-256、签名状态、运行时、输入 shape、类别表和预处理版本。
- GPU 推理使用有界批处理；接近 deadline 时优先处理最新安全相关帧。
- 模型切换：warmup → 固定样例自检 → shadow → 指标对比 → 原子激活。
- 激活版本写入所有输出的 `source.plugin_version`/模型元数据。
- OOM、运行时崩溃或延迟持续超阈值时回退 CPU/轻量基线模型，并标记 `DEGRADED`。

## 8. 配置

建议命名空间：

```yaml
perception:
  fusion:
    window_ms: 100
    max_lateness_ms: 100
    association_gate_mahalanobis: 9.21
  tracks:
    confirm_hits: 3
    confirm_window: 5
    coast_timeout_ms: 600
    lost_timeout_ms: 1500
  deadlines_ms:
    radar: 40
    eo_ir: 80
    fusion: 20
  plugins:
    detector: eo_detector@1.3.0
    fusion: radar_camera_fusion@1.2.0
```

阈值修改必须进入配置版本和 run manifest；LIVE 模式下不能热改会改变安全边界的阈值，除非通过审批流程。

## 9. 错误与降级

| 情况 | 行为 |
|---|---|
| 相机不可用 | 雷达/其他源继续，类别置信度降低 |
| 雷达不可用 | 视觉深度不确定度扩大，近场安全策略收紧 |
| transform 缺失 | 隔离对应数据，不在错误 frame 融合 |
| 模型超时 | 取消推理，使用最近有效航迹预测并增加协方差 |
| 分类冲突 | 输出概率和 `classification_conflict`，不强行选定 |
| 所有感知源过期 | 发布 CRITICAL health；Risk 进入未知/高风险 |

## 10. 外部依赖

- ONNX Runtime/TensorRT/OpenVINO/PyTorch 等经 ModelRuntimePort 使用。
- OpenCV、NumPy、滤波/优化库；版本锁定。
- FrameTransform、Clock、EventBus、Artifact、Metrics 端口。
- 向量索引只存 `embedding_ref`；不成为主跟踪链的强依赖。

## 11. 验收标准

在项目基准数据集/场景上：

| 指标 | 基线 |
|---|---:|
| 雷达+视觉融合目标召回率 | ≥ 0.90（定义的可检测区域） |
| 确认航迹 precision | ≥ 0.92 |
| ID switch | ≤ 2 / 10 min 标准动态场景 |
| 位置 RMSE | ≤ 2.0 m（0–200 m 基准范围） |
| 速度 RMSE | ≤ 1.0 m/s |
| 融合输出 P95 延迟 | ≤ 80 ms |
| 过期/非法数据误融合 | 0 |
| 输出携带 source_refs 和协方差 | 100% |

具体设备能力不满足时必须建立设备级基线，不能删除质量字段或降低安全处理。

## 12. 测试方法

- 单元：门控、关联、滤波、概率融合、状态机、协方差传播。
- 契约：每个插件和 Driver 使用统一 valid/invalid fixtures。
- 数据集：检测 mAP/召回、跟踪 HOTA/MOTA/IDF1、位置/速度误差。
- 变形测试：坐标平移/旋转、时间平移后输出相应变换。
- 故障注入：丢帧、重复、乱序、时钟漂移、错误标定、遮挡、模糊、雨雾、GPU OOM。
- 性能：目标密度、分辨率和帧率矩阵；记录 P50/P95/P99、显存和队列。
- 回放：固定 run 输出规范化 golden tracks，版本升级对比。
