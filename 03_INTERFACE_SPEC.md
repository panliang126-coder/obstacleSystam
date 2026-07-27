# 统一接口规范

## 1. 模块目标

定义跨模块、跨进程和跨设备的唯一数据契约。v1 以 JSON Schema 作为可读和运行时校验格式，同时遵守 protobuf 可兼容演进规则。任何模块不得私自复制并修改核心消息模型。

## 2. 全局约定

### 2.1 命名、类型和单位

- JSON 字段使用 `snake_case`；Topic 使用小写点分层。
- ID 使用 UUIDv7 字符串；版本使用 SemVer。
- 时间使用 UTC RFC 3339，至少毫秒精度，例如 `2026-07-27T03:20:15.123Z`。
- 距离/高度/速度/加速度分别为 `m`、`m_s`、`m_s2`；角度为 `deg`；温度 `deg_c`；压力 `pa`。
- 经纬度为 WGS-84 degree；高度字段必须说明 MSL/AGL/ellipsoid。
- JSON 中不使用 `NaN`/`Infinity`；未知值使用 `null` 并配合 `quality`/`validity`。
- 枚举值使用大写字符串，未知枚举接收方映射为 `UNKNOWN`。

### 2.2 统一事件信封

所有事件外层结构：

```json
{
  "schema": "target/1.0",
  "event_id": "019f3aa0-7c20-7000-8e41-7ba38a7fd010",
  "trace_id": "019f3aa0-7b01-7000-a887-a91da69538ee",
  "causation_id": "019f3aa0-7bf0-7000-9c53-d522ad66c11a",
  "source": {
    "service": "perception-service",
    "instance_id": "edge-01",
    "plugin": "radar_camera_fusion",
    "plugin_version": "1.2.0"
  },
  "observed_at": "2026-07-27T03:20:15.123Z",
  "received_at": "2026-07-27T03:20:15.141Z",
  "monotonic_ns": 2387349381123,
  "run_id": "019f3a90-a001-7000-a107-49d5ce6f81d1",
  "mode": "SIM",
  "vehicle_id": "uav-001",
  "sequence": 4201,
  "quality": {
    "valid": true,
    "confidence": 0.92,
    "clock_uncertainty_ms": 1.4,
    "flags": []
  },
  "payload": {}
}
```

必填：`schema,event_id,trace_id,source,observed_at,received_at,run_id,mode,sequence,quality,payload`。`causation_id` 在根事件可为 `null`，`vehicle_id` 在区域级天气事件可为 `null`。

基础 JSON Schema：

```json
{
  "$id": "https://low-altitude.local/schemas/envelope/1.0.json",
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": [
    "schema", "event_id", "trace_id", "source", "observed_at",
    "received_at", "run_id", "mode", "sequence", "quality", "payload"
  ],
  "properties": {
    "schema": {"type": "string", "pattern": "^[a-z_]+/[0-9]+\\.[0-9]+$"},
    "event_id": {"type": "string", "format": "uuid"},
    "trace_id": {"type": "string", "format": "uuid"},
    "causation_id": {"type": ["string", "null"], "format": "uuid"},
    "source": {
      "type": "object",
      "required": ["service", "instance_id"],
      "properties": {
        "service": {"type": "string"},
        "instance_id": {"type": "string"},
        "plugin": {"type": ["string", "null"]},
        "plugin_version": {"type": ["string", "null"]}
      },
      "additionalProperties": false
    },
    "observed_at": {"type": "string", "format": "date-time"},
    "received_at": {"type": "string", "format": "date-time"},
    "monotonic_ns": {"type": ["integer", "null"], "minimum": 0},
    "run_id": {"type": "string", "format": "uuid"},
    "mode": {"enum": ["SIM", "REPLAY", "HIL", "LIVE"]},
    "vehicle_id": {"type": ["string", "null"]},
    "sequence": {"type": "integer", "minimum": 0},
    "quality": {
      "type": "object",
      "required": ["valid", "confidence", "flags"],
      "properties": {
        "valid": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "clock_uncertainty_ms": {"type": ["number", "null"], "minimum": 0},
        "flags": {"type": "array", "items": {"type": "string"}, "uniqueItems": true}
      },
      "additionalProperties": false
    },
    "payload": {"type": "object"}
  },
  "additionalProperties": false
}
```

## 3. 公共值对象

### 3.1 Position

至少提供一种完整位置表示：

```json
{
  "wgs84": {"lat_deg": 31.2304, "lon_deg": 121.4737, "alt_m_msl": 118.2},
  "enu": {"east_m": 12.4, "north_m": -3.1, "up_m": 18.2},
  "frame_id": "site-alpha-enu-v1",
  "covariance": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 2.25]
}
```

`covariance` 为 3×3 行优先矩阵，单位 `m²`。

### 3.2 Velocity

```json
{
  "frame_id": "site-alpha-enu-v1",
  "east_m_s": 4.1,
  "north_m_s": 0.3,
  "up_m_s": -0.1,
  "covariance": [0.25, 0, 0, 0, 0.25, 0, 0, 0, 0.16]
}
```

### 3.3 SpatialVolume

空间覆盖使用 `POINT`、`POLYGON`、`BOX3D` 或 `GRID`。多边形外环按 WGS-84 `[lon,lat]`，闭合且不自交；3D 体积另带高度上下界。

## 4. Sensor Message

### 4.1 用途与频率

表示驱动层完成解码、基础校验和标定后的单个样本或完整扫描/帧。典型频率：IMU 50–400 Hz、GNSS 1–20 Hz、雷达 5–30 Hz、EO/IR 10–60 Hz、气象 0.1–10 Hz。

Topic：`sensor.normalized.<sensor_type>`；Schema：`sensor/1.0`。

### 4.2 Payload 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `sensor_id` | string | 是 | 稳定设备标识 |
| `sensor_type` | enum | 是 | `RADAR/EO/IR/WEATHER/GNSS/IMU/ACOUSTIC/EM/RADIO/OTHER` |
| `source_session_id` | UUID | 是 | 驱动每次连接产生，用于区分序列重启 |
| `frame_id` | string | 是 | 数据所在坐标系 |
| `calibration_id` | string/null | 是 | 标定版本；无标定需显式为 null 并降低质量 |
| `sample_format` | string | 是 | 载荷格式，例如 `radar_detections_v1` |
| `sample` | object | 是 | 按设备类型注册的子 Schema |
| `raw_ref` | string/null | 否 | 原始大对象 URI/哈希，不内嵌大帧 |
| `diagnostics` | object | 否 | 温度、丢帧、信号质量等 |

### 4.3 示例：雷达检测

```json
{
  "schema": "sensor/1.0",
  "event_id": "019f3aa1-0101-7000-bd8c-81aa65f7f010",
  "trace_id": "019f3aa1-0101-7000-bd8c-81aa65f7f010",
  "causation_id": null,
  "source": {"service": "ingest-service", "instance_id": "edge-01", "plugin": "radar-sim", "plugin_version": "1.0.0"},
  "observed_at": "2026-07-27T03:20:15.100Z",
  "received_at": "2026-07-27T03:20:15.105Z",
  "monotonic_ns": 2387349381000,
  "run_id": "019f3a90-a001-7000-a107-49d5ce6f81d1",
  "mode": "SIM",
  "vehicle_id": "uav-001",
  "sequence": 880,
  "quality": {"valid": true, "confidence": 0.95, "clock_uncertainty_ms": 1.2, "flags": []},
  "payload": {
    "sensor_id": "radar-front-01",
    "sensor_type": "RADAR",
    "source_session_id": "019f3a91-0001-7000-8d21-10ac5812a601",
    "frame_id": "uav-001/radar-front",
    "calibration_id": "radar-front-cal-20260701",
    "sample_format": "radar_detections_v1",
    "sample": {
      "scan_id": 880,
      "detections": [
        {"range_m": 83.2, "azimuth_deg": 4.5, "elevation_deg": -1.0, "radial_velocity_m_s": -6.4, "snr_db": 18.1}
      ]
    },
    "raw_ref": null,
    "diagnostics": {"device_temperature_deg_c": 46.2}
  }
}
```

## 5. Environment Message

### 5.1 用途与频率

描述有空间覆盖、有效时间和不确定度的环境状态及风险因子。典型 0.2–10 Hz；局地快速天气可更高。

Topic：`environment.update`；Schema：`environment/1.0`。

### 5.2 Payload 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `environment_id` | UUID | 是 | 本次估计 ID |
| `valid_from/valid_to` | date-time | 是 | 时间覆盖 |
| `coverage` | SpatialVolume | 是 | 空间覆盖 |
| `grid_ref` | string/null | 否 | 大型栅格对象 URI |
| `wind` | object | 是 | ENU 风矢量、阵风和不确定度 |
| `temperature_deg_c` | number/null | 是 | 温度 |
| `relative_humidity_pct` | number/null | 是 | 0–100 |
| `precipitation_mm_h` | number/null | 是 | 降水率 |
| `visibility_m` | number/null | 是 | 能见度 |
| `pressure_pa` | number/null | 否 | 气压 |
| `risk_factors` | object | 是 | 归一化 0–1 环境风险因子 |
| `provenance` | array | 是 | 观测源、模型和权重 |

### 5.3 示例

```json
{
  "schema": "environment/1.0",
  "event_id": "019f3aa2-1201-7000-8711-46c74153fa91",
  "trace_id": "019f3aa2-1101-7000-bb21-d341a19a70b0",
  "causation_id": "019f3aa2-1181-7000-97b1-5c7e47cf0a70",
  "source": {"service": "weather-service", "instance_id": "edge-01", "plugin": "local_weather_fusion", "plugin_version": "1.0.0"},
  "observed_at": "2026-07-27T03:20:15.000Z",
  "received_at": "2026-07-27T03:20:15.210Z",
  "monotonic_ns": 2387349491000,
  "run_id": "019f3a90-a001-7000-a107-49d5ce6f81d1",
  "mode": "SIM",
  "vehicle_id": "uav-001",
  "sequence": 92,
  "quality": {"valid": true, "confidence": 0.84, "clock_uncertainty_ms": 20.0, "flags": []},
  "payload": {
    "environment_id": "019f3aa2-1200-7000-b1c1-22a4047a2e3c",
    "valid_from": "2026-07-27T03:20:15Z",
    "valid_to": "2026-07-27T03:20:25Z",
    "coverage": {"type": "BOX3D", "frame_id": "site-alpha-enu-v1", "min": [-500,-500,0], "max": [500,500,300]},
    "grid_ref": null,
    "wind": {"east_m_s": 5.1, "north_m_s": -0.8, "up_m_s": 0.2, "gust_m_s": 8.3, "uncertainty_m_s": 1.1},
    "temperature_deg_c": 31.4,
    "relative_humidity_pct": 78.0,
    "precipitation_mm_h": 2.2,
    "visibility_m": 4500,
    "pressure_pa": 100420,
    "risk_factors": {"wind": 0.32, "precipitation": 0.18, "visibility": 0.25, "icing": 0.0, "convective": 0.1},
    "provenance": [{"source_id": "weather-radar-01", "weight": 0.7}, {"source_id": "station-03", "weight": 0.3}]
  }
}
```

## 6. Target Message

### 6.1 用途与频率

表示检测或融合航迹批次。检测可无持久 `track_id`；进入 Twin 的目标必须有系统级 `track_id`。典型 5–30 Hz。

Topic：`perception.targets` 或 `perception.tracks`；Schema：`target/1.0`。

### 6.2 Payload 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `batch_id` | UUID | 是 | 同一推理/融合批次 |
| `frame_id` | string | 是 | 批次统一坐标系 |
| `targets` | array | 是 | 目标列表 |
| `targets[].track_id` | UUID/null | 是 | 检测可 null，航迹必填 |
| `targets[].state` | enum | 是 | `TENTATIVE/CONFIRMED/COASTING/LOST` |
| `targets[].classification` | object | 是 | 类别概率及 top label |
| `targets[].position` | Position | 是 | 位置与协方差 |
| `targets[].velocity` | Velocity/null | 是 | 速度 |
| `targets[].dimensions_m` | object/null | 否 | 长宽高 |
| `targets[].embedding_ref` | string/null | 否 | 向量库引用，不默认内嵌 |
| `targets[].source_refs` | array | 是 | 导致该结果的 Sensor event ID |
| `targets[].age_ms` | integer | 是 | 相对当前输出的观测年龄 |

### 6.3 示例（省略通用信封重复字段）

```json
{
  "schema": "target/1.0",
  "payload": {
    "batch_id": "019f3aa3-0101-7000-90c3-08857945120f",
    "frame_id": "site-alpha-enu-v1",
    "targets": [{
      "track_id": "019f3aa0-3310-7000-bb09-d79100dd702c",
      "state": "CONFIRMED",
      "classification": {"top_label": "UAV", "probabilities": {"UAV": 0.88, "BIRD": 0.09, "UNKNOWN": 0.03}},
      "position": {"enu": {"east_m": 83.0, "north_m": 6.5, "up_m": 19.2}, "frame_id": "site-alpha-enu-v1", "covariance": [1,0,0,0,1.2,0,0,0,1.5]},
      "velocity": {"frame_id": "site-alpha-enu-v1", "east_m_s": -6.2, "north_m_s": 0.1, "up_m_s": 0.0, "covariance": [0.4,0,0,0,0.4,0,0,0,0.3]},
      "dimensions_m": {"length": 0.8, "width": 0.8, "height": 0.3},
      "embedding_ref": "vector://run/track/019f3aa0-3310",
      "source_refs": ["019f3aa1-0101-7000-bd8c-81aa65f7f010"],
      "age_ms": 35
    }]
  }
}
```

## 7. Risk Message

### 7.1 用途与频率

描述某车辆、任务、轨迹或航段的综合与分项风险。事件触发并支持 2–10 Hz 周期刷新。

Topic：`risk.update`；Schema：`risk/1.0`。

### 7.2 Payload 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `risk_id` | UUID | 是 | 评估 ID |
| `subject` | object | 是 | `VEHICLE/TRACK/PATH/MISSION/AREA` 及 ID |
| `twin_revision` | integer | 是 | 使用的 Twin 版本 |
| `horizon_s` | number | 是 | 预测窗口 |
| `score` | number | 是 | 0–100，越高越危险 |
| `level` | enum | 是 | `LOW/MODERATE/HIGH/CRITICAL/UNKNOWN` |
| `dimensions` | object | 是 | weather/collision/energy/communication/system 0–100 |
| `explanations` | array | 是 | 结构化原因、证据和建议 |
| `valid_until` | date-time | 是 | 超过即不可用于新决策 |
| `recommended_constraints` | array | 是 | 供规划/决策使用的约束 |

示例：

```json
{
  "schema": "risk/1.0",
  "payload": {
    "risk_id": "019f3aa4-0101-7000-ad1a-3d91adad8083",
    "subject": {"type": "VEHICLE", "id": "uav-001"},
    "twin_revision": 18722,
    "horizon_s": 15,
    "score": 76.0,
    "level": "HIGH",
    "dimensions": {"weather": 32, "collision": 88, "energy": 21, "communication": 10, "system": 15},
    "explanations": [{
      "code": "CLOSING_TRACK",
      "severity": "HIGH",
      "summary": "前方航迹在 4.2 秒内进入保护区",
      "evidence": {"track_id": "019f3aa0-3310-7000-bb09-d79100dd702c", "tcpa_s": 4.2, "dcpa_m": 6.1}
    }],
    "valid_until": "2026-07-27T03:20:15.700Z",
    "recommended_constraints": [{"type": "EXCLUSION_CYLINDER", "center_enu_m": [58.2, 6.8, 19.3], "radius_m": 20, "valid_for_s": 8}]
  }
}
```

## 8. Path Message

### 8.1 用途与频率

描述候选或已选路径、约束、代价和有效性。任务级低频，局部路径在动态环境中 2–20 Hz。

Topic：`path.proposed`/`path.selected`；Schema：`path/1.0`。

### 8.2 Payload 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `path_id` | UUID | 是 | 路径 ID |
| `mission_id` | string | 是 | 任务 ID |
| `planner` | object | 是 | 插件名、版本、算法 |
| `twin_revision` | integer | 是 | 使用的世界版本 |
| `risk_id` | UUID | 是 | 使用的风险结果 |
| `frame_id` | string | 是 | 航点坐标系 |
| `waypoints` | array | 是 | 至少 2 个，含位置、速度和预计到达 |
| `costs` | object | 是 | distance/time/energy/risk/total |
| `constraints_applied` | array | 是 | 已应用约束 |
| `validation` | object | 是 | 碰撞、动力学、地理围栏校验 |
| `valid_until` | date-time | 是 | 路径有效期 |
| `status` | enum | 是 | `CANDIDATE/SELECTED/REJECTED/EXPIRED` |

示例：

```json
{
  "schema": "path/1.0",
  "payload": {
    "path_id": "019f3aa5-0101-7000-a01f-5f673f9cba2f",
    "mission_id": "mission-20260727-001",
    "planner": {"name": "local_mpc", "version": "1.1.0", "algorithm": "MPC"},
    "twin_revision": 18722,
    "risk_id": "019f3aa4-0101-7000-ad1a-3d91adad8083",
    "frame_id": "site-alpha-enu-v1",
    "waypoints": [
      {"seq": 0, "enu_m": [0,0,20], "target_speed_m_s": 6, "eta_s": 0},
      {"seq": 1, "enu_m": [30,-18,24], "target_speed_m_s": 5, "eta_s": 6.4},
      {"seq": 2, "enu_m": [80,-10,25], "target_speed_m_s": 6, "eta_s": 15.0}
    ],
    "costs": {"distance": 1.0, "time": 0.8, "energy": 0.7, "risk": 0.3, "total": 2.8},
    "constraints_applied": ["geofence-v3", "vehicle-envelope-uav001", "risk:019f3aa4-0101"],
    "validation": {"collision_free": true, "dynamics_feasible": true, "geofence_valid": true, "minimum_clearance_m": 18.5},
    "valid_until": "2026-07-27T03:20:15.600Z",
    "status": "CANDIDATE"
  }
}
```

## 9. Decision Message

### 9.1 用途与频率

表达可解释策略，而非直接电机/姿态命令。事件触发，必要时 2–10 Hz 刷新。Topic：`decision.proposed`、`decision.authorized`、`decision.rejected`；Schema：`decision/1.0`。

### 9.2 Payload 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `decision_id` | UUID | 是 | 决策 ID |
| `mission_id` | string | 是 | 任务 ID |
| `action` | enum | 是 | `CONTINUE/AVOID/HOLD/RETURN/LAND/ABORT` |
| `priority` | integer | 是 | 0–100 |
| `path_id` | UUID/null | 是 | 需要路径时必填 |
| `risk_id` | UUID | 是 | 使用的风险评估 |
| `twin_revision` | integer | 是 | 使用的 Twin 版本 |
| `reason_codes` | array | 是 | 机器可读原因 |
| `explanation` | string | 是 | 面向操作员说明 |
| `preconditions` | array | 是 | 执行前条件 |
| `expires_at` | date-time | 是 | 过期后不得执行 |
| `policy` | object | 是 | 决策插件及规则集版本 |
| `authorization` | object | 是 | `PENDING/AUTHORIZED/REJECTED` 与门控结果 |

示例：

```json
{
  "schema": "decision/1.0",
  "payload": {
    "decision_id": "019f3aa6-0101-7000-b8f6-d17417e1c921",
    "mission_id": "mission-20260727-001",
    "action": "AVOID",
    "priority": 85,
    "path_id": "019f3aa5-0101-7000-a01f-5f673f9cba2f",
    "risk_id": "019f3aa4-0101-7000-ad1a-3d91adad8083",
    "twin_revision": 18722,
    "reason_codes": ["COLLISION_RISK_HIGH", "ALTERNATE_PATH_VALID"],
    "explanation": "前方动态目标碰撞风险高，执行已验证的右侧局部绕行路径。",
    "preconditions": ["vehicle_state_age_ms<100", "path_valid=true", "control_link=healthy"],
    "expires_at": "2026-07-27T03:20:15.600Z",
    "policy": {"name": "baseline_safety_policy", "version": "1.0.0", "ruleset_hash": "sha256:..."},
    "authorization": {"state": "AUTHORIZED", "gate": "safety-gate-01", "checked_at": "2026-07-27T03:20:15.250Z", "failures": []}
  }
}
```

## 10. 补充控制与状态消息

六类核心消息之外，闭环至少还需：

- `vehicle.state/1.0`：位置、姿态、速度、电量、飞行模式、armed、链路和 failsafe。
- `mission.command/1.0`：创建/暂停/恢复/取消任务，必须带 `idempotency_key`。
- `control.command/1.0`：仅 Safety Gate 到 Control Gateway；含目标 setpoint/mission item、deadline、授权令牌摘要。
- `control.ack/1.0`：`ACCEPTED/EXECUTING/COMPLETED/REJECTED/TIMEOUT`。
- `health/1.0`：组件状态、依赖、数据新鲜度和结构化故障。
- `twin.snapshot/1.0`：revision、实体引用、环境版本和地图版本。

这些消息在首次实现前必须在 `schemas/` 中补齐正式 Schema 和示例。

Phase 2 已固化以下补充消息字段：

- `health/1.0`：`component_id`、`component_type`、`status`、`checked_at`、
  `dependencies`、`data_freshness_ms` 和 `faults`。状态只允许
  `HEALTHY/DEGRADED/UNHEALTHY/STOPPED/UNKNOWN`。
- `twin.snapshot/1.0`：`twin_id`、单调 `revision`、`as_of`、`watermark`、
  `frame_id`、`map_version`、`config_hash`、vehicle/sensor/track/environment/health
  状态引用、`input_refs`、`staleness` 和聚合 `quality`。引用包含生成该状态的
  revision；安全消费者不得用未固定 revision 的“latest”引用。

## 11. Topic 注册表

| Topic | Schema | 默认 QoS/持久化 | 权限 |
|---|---|---|---|
| `sensor.raw.<type>` | vendor/raw | 可丢，不默认持久 | driver 发布 |
| `sensor.normalized.<type>` | `sensor/1.0` | 高频，选择性持久 | driver 发布，业务读 |
| `perception.targets` | `target/1.0` | 至少一次 | perception 发布 |
| `perception.tracks` | `target/1.0` | 至少一次 | fusion 发布 |
| `environment.update` | `environment/1.0` | 保留最新+历史 | weather 发布 |
| `twin.snapshot` | `twin.snapshot/1.0` | revision 持久 | twin 发布 |
| `risk.update` | `risk/1.0` | 至少一次 | risk 发布 |
| `path.proposed` | `path/1.0` | 至少一次 | planner 发布 |
| `decision.proposed` | `decision/1.0` | 持久、不可静默丢 | decision 发布 |
| `decision.authorized` | `decision/1.0` | 持久、不可静默丢 | safety gate 发布 |
| `control.command` | `control.command/1.0` | 持久、幂等 | safety gate 发布，gateway 读 |
| `control.ack` | `control.ack/1.0` | 持久 | gateway 发布 |
| `health.update` | `health/1.0` | 保留最新+历史 | 服务发布 |
| `management.command` | command schema | 持久、幂等 | 授权客户端发布 |
| `deadletter.<topic>` | error envelope | 受限持久 | bus/validator 发布 |

## 12. 插件接口

```python
@dataclass(frozen=True)
class PluginContext:
    run_id: UUID
    mode: RuntimeMode
    clock: ClockPort
    event_bus: EventBusPort
    metrics: MetricsPort
    config: Mapping[str, Any]

class Plugin(Protocol):
    @property
    def manifest(self) -> PluginManifest: ...
    async def initialize(self, context: PluginContext) -> None: ...
    async def health(self) -> HealthStatus: ...
    async def shutdown(self, deadline_s: float) -> None: ...

class PerceptionPlugin(Plugin, Protocol):
    async def process(self, batch: Sequence[SensorMessage]) -> TargetMessage: ...

class WeatherPlugin(Plugin, Protocol):
    async def estimate(self, observations: Sequence[SensorMessage]) -> EnvironmentMessage: ...

class RiskPlugin(Plugin, Protocol):
    async def assess(self, snapshot: TwinSnapshot) -> RiskMessage: ...

class PlannerPlugin(Plugin, Protocol):
    async def plan(self, request: PlanRequest) -> PathMessage: ...

class DecisionPolicy(Plugin, Protocol):
    async def decide(self, context: DecisionContext) -> DecisionMessage: ...
```

接口参数是不可变领域对象。插件通过返回值/受控 Event Bus 产生输出，不修改输入或全局单例。

## 13. Driver 接口

```python
class SensorDriver(Protocol):
    @property
    def descriptor(self) -> SensorDescriptor: ...
    async def connect(self) -> None: ...
    async def samples(self) -> AsyncIterator[SensorMessage]: ...
    async def health(self) -> HealthStatus: ...
    async def close(self) -> None: ...
```

真实与模拟驱动必须通过相同测试套件：

- 生命周期幂等；
- 断线、重连、序列和 session 行为；
- 时间、frame、标定和 quality 字段；
- 背压/取消；
- 非法源数据处理；
- 不在 `samples()` 之外泄漏厂商类型。

## 14. 查询与命令 API

- Query API 可使用 REST/gRPC；必须分页、带超时、限制时间范围。
- Command API 使用显式命令资源，不用通用 PATCH 改安全状态。
- 所有有副作用请求携带 `idempotency_key`、请求者和期望 revision。
- 并发更新使用 optimistic concurrency；revision 不匹配返回冲突。
- 错误结构统一：`code,message,retryable,details,trace_id`。

## 15. protobuf 兼容规则

1. 每个 JSON 字段对应稳定 protobuf field number。
2. 已发布 field number 永不复用；删除字段使用 `reserved`。
3. 新增字段必须可选并有安全默认语义。
4. enum 的 `0` 固定为 `*_UNSPECIFIED`；只追加，不重编号。
5. `oneof` 用于互斥坐标/载荷表示；不得把既有字段移入 `oneof` 造成不兼容。
6. 时间使用 `google.protobuf.Timestamp`，持续时间用 `Duration`。
7. 版本不依赖 protobuf package 名隐式推断，Envelope 仍带 `schema`。
8. CI 对新旧 descriptor 执行 breaking-change 检查。

## 16. Schema 演进

- Patch：描述、示例、非语义校验修复；消费者无感。
- Minor：新增可选字段/枚举；旧消费者可忽略。
- Major：删除、重命名、单位/语义改变；必须双写/双读并定义退役期限。
- 生产者只在消费者兼容矩阵通过后启用新版本。
- 未知 major 版本必须拒绝；未知 minor 字段应忽略并保留。

## 17. 验收标准

1. 六类核心消息均有可执行 JSON Schema、正反示例和 protobuf 映射。
2. 示例通过 Schema 校验，非法单位、时间、坐标、枚举和缺字段用例均失败。
3. 真实/模拟 Driver 通过同一契约测试。
4. 插件清单与输入/输出 Schema 不兼容时加载失败。
5. minor 升级的旧消费者兼容测试通过；breaking change 被 CI 阻止。
6. 控制命令重复发送不产生重复副作用。

## 18. 测试方法

- 基于 Schema 自动生成边界数据和 property-based tests。
- 对每个消息维护 `valid/`、`invalid/`、`compat/` fixture。
- JSON ↔ protobuf 双向转换并比较规范化语义。
- 测试未知字段、未知 enum、旧 minor、重复事件、超大 payload。
- 使用 Driver Contract Test 和 Plugin Contract Test 作为所有实现的强制测试。
