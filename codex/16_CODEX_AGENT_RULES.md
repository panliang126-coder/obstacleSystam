# Codex Agent 开发规范

## 1. 目标

规范 Codex/Agent 在本项目中的理解、实现、测试、文档和交付行为，使每次改动范围明确、接口兼容、证据充分且可安全回滚。

本规则不能替代 [00_MASTER.md](../00_MASTER.md) 的架构和安全基线。

## 2. 开始任务前必须阅读

按顺序：

1. [00_MASTER.md](../00_MASTER.md)；
2. [03_INTERFACE_SPEC.md](../03_INTERFACE_SPEC.md)；
3. 当前任务对应的模块文档；
4. [14_TEST_PLAN.md](../test/14_TEST_PLAN.md)；
5. 当前目录向上适用的 `AGENTS.md`（若未来存在）；
6. 相关代码、测试、配置和最近变更。

不得只根据文件名或用户摘要假设实现。

## 3. 任务分类

开始前确定一种主要类型：

- **解释/审查**：只读检查并报告，不修改。
- **诊断**：定位根因并给证据；未要求修复时不实施。
- **实现/修复**：修改最小范围、补测试并验证。
- **接口变更**：先文档/Schema/兼容计划，再代码。
- **设备接入**：先端口/模拟或录制 fixture/契约测试，再真实 Adapter。
- **安全关键**：Risk、Planning、Decision、Safety Gate、Control、LIVE 配置；要求扩大验证和明确剩余风险。

任务不清晰但可安全推断时记录假设继续；会改变安全行为、协议 major 或真实设备副作用时必须向用户确认。

## 4. 标准开发流程

```text
Understand
  -> Inspect
  -> Define scope/interfaces
  -> Add or update failing test
  -> Implement minimal change
  -> Run focused tests
  -> Run contract/integration/scenario tests as risk requires
  -> Review diff
  -> Update docs/schema/examples
  -> Report evidence and residual risk
```

### 4.1 理解

输出/记录：

- 目标和不做的内容；
- 影响模块、运行模式和用户；
- 输入/输出 Schema、Topic、Repository/Plugin/Driver 端口；
- deadline、资源、安全和兼容约束；
- 验收标准。

### 4.2 检查

- 搜索同类实现、测试、配置、Schema 和文档。
- 检查版本状态和用户未提交改动；不覆盖无关修改。
- 先复现缺陷；无法复现时保存日志/环境和最小反例。
- 确认真实/模拟路径是否共用契约。

### 4.3 实现

- 领域逻辑依赖端口，不直接依赖 SDK/Broker/ORM/GUI。
- 业务时间使用 `ClockPort`，随机性使用 `RandomProvider`。
- 有界队列、deadline、取消和清理路径必须实现。
- 事件携带 trace/causation/run/source/quality/version。
- 对外副作用必须幂等；重要状态采用 revision/CAS。
- 失败行为显式，禁止吞异常或返回伪造成功。

### 4.4 验证

最低要求：

| 变更 | 必须执行 |
|---|---|
| 纯函数/小修复 | 相关单元 + 回归 |
| Message/Schema | Schema valid/invalid + 兼容 + JSON/protobuf |
| Driver | 共享 Driver Contract + 断线/重连 |
| Plugin | Plugin Contract + deadline/crash/rollback |
| Repository/Event Bus | 真实依赖容器集成 + 幂等/恢复 |
| Perception/Weather 算法 | 单元 + 数据集指标 + 回放 + 性能 |
| Risk/Planning/Decision | 单元 + 安全场景 + 端到端 SIL |
| GUI | ViewModel + Qt 交互 + 断线 + 性能/截图 |
| Deployment | smoke + 健康 + 升级/回滚 |
| Control/LIVE | SIL + HIL + 安全评审；不可只跑 mock |

未能运行的测试必须说明原因、影响和用户可执行的精确命令；不能写“应该通过”。

## 5. 代码结构规则

### 5.1 依赖

允许：

```text
domain <- application <- ports <- adapters/composition
```

更准确地说，`domain` 定义模型，`ports` 定义抽象，`application` 编排领域与端口，`adapters` 实现端口，`app` 组合。业务模块只能通过公共领域消息/端口交互。

禁止：

- 业务模块导入另一模块的 `internal`；
- 领域层导入 PyQt、数据库、Broker、厂商 SDK；
- GUI 连接设备 SDK/私有数据库；
- 模拟器 truth 类型进入生产业务模块；
- 插件直接取得 Control Gateway；
- 用全局可变单例共享业务状态。

### 5.2 Python 基线

- Python 3.11+，类型注解覆盖公共 API。
- 公共 DTO 优先不可变；枚举、单位和坐标明确。
- 使用结构化异常层次和稳定 error code。
- 异步函数不调用阻塞 I/O；阻塞 SDK 放受控线程/进程 Adapter。
- 资源使用 async context manager/明确 shutdown。
- 不使用裸 `except:`，不静默忽略 `CancelledError`。
- 日志使用参数化结构字段，不拼接 Secret/大 payload。

### 5.3 配置

- 配置有 Schema、默认、单位、范围、作用域和热加载声明。
- 不在代码中散落安全阈值或 endpoint。
- 每次运行记录有效配置 hash。
- Secret 只使用引用/注入，不提交明文。

## 6. 接口变更规则

修改核心消息时：

1. 定位生产者、消费者、存储、GUI、回放和外部接口。
2. 判断 patch/minor/major。
3. 先修改 `03_INTERFACE_SPEC.md` 和正式 Schema/proto。
4. 添加 valid、invalid、old/new compatibility fixture。
5. minor 新字段必须可选且有安全默认。
6. major 定义双写/双读、upcaster、数据迁移、期限和回滚。
7. 运行 breaking-change 工具与消费者契约测试。
8. 更新 Topic/数据库投影/模块文档和 `00_MASTER.md` 索引（如需要）。

绝不：

- 复用 protobuf field number；
- 把单位/坐标语义改掉而不升 major；
- 删除未知/质量/版本字段以“简化”；
- 只改某个模块内复制的 DTO。

## 7. 新设备接入规则

顺序：

1. 定义/复用 `SensorDriver` 或 `FlightControllerPort`；
2. 明确设备 frame、时间基准、单位、频率、标定、错误和重连；
3. 建立模拟 Driver 或录制 fixture；
4. 编写共享 Driver Contract Test；
5. 在 Adapter 中实现 SDK/串口/CAN/UDP/ROS2/MQTT/MAVLink；
6. 执行硬件契约、故障注入和 HIL；
7. 只在适配器边界做厂商类型与 ENU/领域类型转换；
8. 更新部署权限、端口、Secret 和 Runbook。

上层感知/风险/规划代码因设备品牌而修改，通常表示端口设计失败，应先修复抽象。

## 8. 插件开发规则

插件必须：

- 提供 manifest、SemVer、API/Schema 兼容范围和资源需求；
- 导入无副作用；
- 支持 initialize/health/deadline/cancel/shutdown；
- 输出通过 Schema 校验；
- 附单元、契约、性能和适用场景报告；
- 支持 shadow 和回滚；
- 不直接写私有外部状态，除非通过授权端口；
- 把模型/artifact hash 写入输出来源。

学习插件不得替代硬安全规则和独立 Validator/Safety Gate。

## 9. 测试代码规则

- 测试名称表达行为和条件，不复制实现步骤。
- 使用 fake clock/RNG，不用真实 sleep 等待业务超时。
- 每个测试可独立、可并行、无顺序依赖。
- fixture 数据小而可读；大数据集使用 hash/manifest 引用。
- Mock 外部边界，不 Mock 被测领域逻辑。
- 断言输出语义、状态和副作用，不只断言“未抛异常”。
- 对浮点使用领域容差；记录单位。
- golden 更新必须审查语义 diff，禁止盲目重写。

## 10. 安全关键变更

以下属于安全关键：

- Risk 分数/等级/硬规则；
- 路径硬约束、碰撞检查、动力学验证；
- Decision 状态机、优先级、超时；
- Safety Gate/Control Gateway；
- frame、时间、单位；
- LIVE 权限、endpoint、飞控适配器；
- 关键数据过期/降级。

要求：

- 明确 hazard 和失败后果；
- 添加正常、边界、反例、过期和异常测试；
- 运行至少相关 SIL 场景；真实控制还需 HIL；
- 不以“更易用”为由 fail-open；
- 保留旧行为回滚；
- 在交付中列出未验证范围。

## 11. 禁止事项

- 未经明确请求修改无关代码或大规模重构。
- 覆盖/删除用户未提交变更。
- 硬编码密码、令牌、私钥、生产 IP/端口凭据。
- 在日志、测试快照或错误中泄露 Secret。
- 通过删除测试、放宽阈值或加无说明重试让 CI 变绿。
- 使用无界队列、无 deadline 网络调用或阻塞 event loop。
- 捕获异常后继续发布 valid/authorized 结果。
- 用最后数据无限期冒充实时数据。
- 在 REPLAY/SIM 中连接真实控制设备。
- UI、插件、脚本绕过 Decision/Safety Gate 直连飞控。
- 未验证 Schema/Artifact/配置 hash 就加载。
- 只报告“完成”而没有验证证据。

## 12. 提交与变更说明

若项目启用 Git，建议 Conventional Commit：

```text
feat(perception): add radar-camera association plugin
fix(decision): reject expired path before authorization
docs(interface): define control acknowledgement schema
test(simulator): cover GNSS clock drift scenario
```

每个提交：

- 单一目的，可审查；
- 代码、测试和必要文档一起提交；
- 不包含生成缓存、Secret、大模型二进制（使用 artifact 引用）；
- breaking change 在正文和 ADR 标明；
- 不重写用户历史，除非明确授权。

## 13. 文档更新矩阵

| 变更 | 更新文档 |
|---|---|
| 系统边界/依赖/进程 | 00、01、相关 ADR |
| 数据顺序/背压/超时 | 02 |
| Message/Topic/API/Plugin/Driver | 03 |
| 表/索引/保留/迁移 | 04 |
| 模块算法/阈值/验收 | 对应 05–13 |
| 测试层级/场景/指标 | 14 |
| 镜像/配置/Secret/运维 | 15 |
| Agent 工作方式 | 16 |

文档描述必须与可执行 Schema、测试和配置一致。

## 14. Agent 交付格式

最终汇报至少包含：

1. **结果**：完成了什么可观察能力。
2. **改动**：关键文件/模块和接口。
3. **验证**：实际运行命令与通过结果、指标。
4. **兼容/安全**：Schema/配置/部署影响和回滚。
5. **剩余事项**：未运行/未覆盖/外部阻塞；没有则明确无。

不要要求用户阅读中间过程才能理解最终结果。

## 15. 完成定义

只有同时满足以下条件才能标记完成：

- 实现与任务验收一致；
- 相关单元/契约/集成/场景测试实际通过；
- lint/type/Schema 检查通过；
- 新接口有文档、示例和兼容策略；
- 错误、取消、超时、资源清理和降级已处理；
- 无新增 Secret/未授权副作用；
- diff 已自审，无调试残留和无关改动；
- 交付报告含证据和剩余风险。

预算不足或测试环境不可用不是“完成”，应明确标记未验证。
