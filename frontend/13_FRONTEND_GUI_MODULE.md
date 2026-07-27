# PyQt 可视化前端模块

## 1. 模块目标

提供面向操作员、工程师和安全审核人员的实时态势、数字孪生、目标、天气、风险、路径、健康、日志和历史回放界面。前端通过公共 Query/Command API 与事件订阅工作，不直接访问业务模块对象、私有数据库或飞控设备。

## 2. 模块职责

- 显示系统/车辆/传感器/插件/链路健康。
- 显示 2D 地图、可选 3D Twin、动态目标、天气覆盖、风险区域和路径。
- 展示 Decision 原因、状态机、授权与 Control Ack。
- 支持历史查询、回放、时间轴和事件追踪。
- 提供受 RBAC 保护的任务、模式、配置、插件和告警操作。
- 保证 UI 线程安全、有界更新、断线重连和数据新鲜度可见。

不负责计算风险、验证路径或绕过 Safety Gate。UI 消失不应停止数据面。

## 3. 用户角色与工作区

| 工作区 | 主要角色 | 功能 |
|---|---|---|
| 运行总览 | Viewer/Operator | 车辆、任务、总体风险、关键告警 |
| 地图态势 | Operator | 目标、天气、地理围栏、路径、决策 |
| 数字孪生 | Operator/Engineer | 3D/时间预测/分支推演结果 |
| 设备与健康 | Engineer | 传感器、服务、队列、数据年龄、资源 |
| 风险与决策 | Operator/Safety | 分项风险、证据、策略状态和 Ack |
| 回放与分析 | Engineer/Safety | run、时间轴、trace、结果对比 |
| 配置/插件/模型 | Engineer/Approver | 草稿、校验、影子、审批、回滚 |
| 日志与审计 | Engineer/Admin | 结构化查询、导出、审计 |

角色决定可见操作；服务端仍需再次授权。

## 4. 页面布局

```text
+------------------------------------------------------------------+
| Site | Vehicle | Mode | Connection | UTC | User | Alerts         |
+---------------------+--------------------------------------------+
| Navigation          | Main Workspace                             |
| - Overview          | Map / Digital Twin / Replay                |
| - Situation         |                                            |
| - Risk & Decision   |                                            |
| - Health            +--------------------------------------------+
| - Replay            | Context Panel                              |
| - Management        | Target / Risk / Path / Decision details    |
+---------------------+--------------------------------------------+
| Event timeline | Log summary | Queue/Data age | Control Ack       |
+------------------------------------------------------------------+
```

颜色不能作为唯一状态编码；状态同时使用文本、图标/形状和可访问说明。

## 5. 内部结构与技术架构

```text
PyQt6 Views / Widgets / QML(optional)
             |
       ViewModels / Models
             |
 UI State Store + Selection/Timeline State
             |
  Event Stream Client       Query/Command Client
             |                      |
   Background I/O Loop / Worker Threads
             |
      Management/API/Event Gateway
```

建议采用 MVVM：

- View：渲染和用户事件，不做业务计算。
- ViewModel：格式化、选择、过滤、命令确认。
- UI State Store：按 entity ID 保存最新不可变视图状态。
- Client/Adapter：WebSocket/gRPC/MQTT（只选一种正式路径）和 REST/gRPC Query。

## 6. 线程与刷新

- Qt GUI 对象只在主线程访问。
- 网络、反序列化、大数据查询和 3D 几何处理在 worker/异步线程。
- worker 通过 Qt Signal/queued connection 传不可变 DTO。
- UI 更新以实体 ID 合并，避免每个高频事件都重绘。
- 地图/目标刷新默认 10–20 Hz；系统状态 2–5 Hz；日志按批次 5–10 Hz。
- 后台队列有界；满时保留最新状态，Risk/Decision/Alert 不丢。
- 页面不可见时降低刷新率，但继续维护关键状态/告警。

目标是 UI 数据显示延迟可见且不反压数据面。

## 7. 输入

| 数据 | 来源 | 展示 |
|---|---|---|
| Vehicle/Twin | 事件/Query | 位置、姿态、状态、revision、新鲜度 |
| Target | `perception.tracks` | 航迹、类别概率、不确定区、年龄 |
| Environment | `environment.update` | 风、雨、能见度、coverage、unknown |
| Risk | `risk.update` | 总分、等级、维度、证据、有效期 |
| Path | `path.*` | 候选/选中路径、约束、净空、有效期 |
| Decision/Ack | `decision.*`,`control.ack` | 状态机、原因、授权、执行 |
| Health/Alert | `health.update` | 组件依赖、数据新鲜度、告警 |
| Logs/Audit | Query API | trace、actor、变更、错误 |

前端必须显示数据 `age` 和连接状态，断线后不得继续把旧数据呈现为实时。

## 8. 接口与输出命令

前端可发：

- 任务创建/暂停/恢复/取消请求；
- 告警确认；
- SIM/REPLAY run 控制；
- 配置草稿、校验、审批/激活请求；
- 插件/模型影子、激活、回滚请求；
- LIVE 高风险动作的操作员确认（若政策要求）。

每次命令包含 `idempotency_key,actor,client_time,expected_revision,reason`。按钮发送后进入 pending，依据 operation/事件结果更新，不能因客户端 timeout 直接重复生成新 key。

## 9. 地图与数字孪生显示

### 9.1 2D 地图

- 瓦片/地图版本、ENU 原点和 WGS-84 转换一致。
- 目标图标显示 heading、速度、类别和 track 状态。
- 协方差使用椭圆/体积，不能只显示精确点。
- 天气/风险层区分 observed、forecast、unknown。
- Path 显示状态、时间方向、候选/选中和失效。
- 地理围栏和净空为显式图层，可查看来源/有效期。

### 9.2 3D Twin

渲染适配器接收简化 Scene DTO，不依赖 Twin 内部实体。大网格/模型按 LOD；渲染失败不影响 2D 安全态势。3D 中必须标识预测/推演与实时状态，避免操作员混淆。

## 10. 风险与决策 UX

- 顶部显示最高风险和数据新鲜度。
- 风险详情按维度排序，展示 explanation code、证据值、阈值、来源和建议约束。
- Decision 显示 `PROPOSED/AUTHORIZED/SENT/ACK/COMPLETED`，不能把 proposed 显示为已执行。
- Safety Gate rejection 显示具体失败项。
- CRITICAL 告警需要明确确认，但确认只表示已读，不降低风险。
- 自动动作倒计时必须由服务端 deadline 驱动，不能依赖本机 UI 时钟做授权。

## 11. 历史回放

- 选择 `run_id`、时间范围、Topic/实体和速度。
- 时间轴支持 pause/seek/step；显示回放水位线和缺口。
- UI 切换到 REPLAY 使用独立色带/水印，隐藏或禁用 LIVE 控制。
- 可并排比较两个 run 的 Risk/Path/Decision 和关键指标。
- seek 后清空依赖旧时间状态的临时选择/动画，按 checkpoint 重建。

## 12. 连接与离线行为

状态：`CONNECTING -> SYNCING -> LIVE/REPLAY -> DEGRADED -> DISCONNECTED`。

- 重连后先从 Query API 取 revision 快照，再从该 cursor 接事件，避免状态空洞。
- 检测 sequence/revision 缺口时标记不完整并重新同步。
- 断线时冻结最后状态、覆盖灰色/明确时间，不允许发送控制类命令。
- 本地只缓存非敏感 UI 偏好；命令和权限不离线缓存执行。

## 13. 配置与可访问性

- 语言、单位显示、地图主题、刷新率和图层为用户偏好。
- 领域内部仍使用统一 SI/UTC，显示转换不回写领域数据。
- 支持键盘导航、合理焦点、色盲可辨色板、高 DPI。
- 高风险确认对话框说明车辆、动作、原因、有效期；禁止仅“确定/取消”的模糊文案。

## 14. 错误与降级

| 情况 | 行为 |
|---|---|
| 事件流断开 | 标记断线、冻结/变灰、禁用命令、自动退避重连 |
| 地图服务失败 | 使用本地基础图/无地图态势，仍显示相对坐标 |
| 3D 渲染失败 | 回退 2D，不影响其他页面 |
| 大查询超时 | 取消、缩小范围建议，不冻结 UI |
| 非法消息 | 隔离到客户端诊断计数，不使 UI 崩溃 |
| 命令结果未知 | 查询 operation/idempotency 状态，不盲重发 |

## 15. 外部依赖

PyQt6、地图/绘图/3D 渲染适配器、API Client、身份客户端。依赖均在 UI Adapter 层，不导入后端模块实现。

## 16. 验收标准

1. 标称 1000 航迹、20 Hz 输入下 UI 主线程 P95 事件处理不超过 16 ms，持续操作无明显冻结。
2. Risk/Decision/Alert 显示延迟 P95 ≤ 250 ms。
3. 断线 ≤ 1 s 明确显示，旧数据均显示年龄且高风险命令禁用。
4. proposed、authorized、executing、completed 状态不混淆。
5. GUI 无直接数据库、设备 SDK 或 Control Gateway 连接。
6. 回放模式向 LIVE 命令 API 成功请求数为 0。
7. 关键页面键盘操作、缩放和色彩可辨性测试通过。

### 16.1 Phase 6 可执行基线

- `UiStateStore` 按实体合并高频消息，检测 sequence 缺口，显示数据 age，并分别限制
  普通实体与已处理关键事件历史容量。
- `ReplayController` 支持 pause/play/seek/step；每次 seek 清空临时状态并从事件序列
  重建，REPLAY Store 不允许发送 LIVE 管理命令。
- PyQt6 Adapter 提供 Overview、Situation、Risk & Decision、Health、Replay 和
  Management 六个工作区；Snapshot 通过 queued Signal 进入主线程。
- Decision 的 PENDING/AUTHORIZED/REJECTED 和 Control Ack 使用独立状态文本，
  不以颜色作为唯一编码。
- Linux offscreen Qt 测试覆盖 Signal、连接状态、管理视图、告警确认和 1000 航迹
  模型更新 P95 16 ms 帧预算。
- 地图瓦片、3D Scene、真实 Query/Event Client 与视觉 golden 尚未接入，不能宣称
  已完成现场级 GUI 验收。

## 17. 测试方法

- ViewModel/State Store 单元测试；Qt Model 行列、更新和 selection 测试。
- 使用模拟 Event/API Client 做组件测试，覆盖重复、乱序、缺口、断线和重连。
- `pytest-qt` 或等价工具测试 Signal、主线程和用户操作。
- 视觉截图/golden 测试不同 DPI、主题、语言、空数据、告警状态。
- 大量航迹、天气网格、日志流性能和内存测试。
- RBAC、命令幂等、确认、timeout/unknown result 测试。
- 操作员可用性演练：发现风险、解释原因、确认状态、执行回放。
