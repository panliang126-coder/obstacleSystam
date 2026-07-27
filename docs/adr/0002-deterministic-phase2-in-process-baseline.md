# ADR-0002：Phase 2 确定性单进程基线

- 状态：Accepted
- 日期：2026-07-27
- 决策者：项目 Phase 2 基线

## 背景

在引入外部 Broker、数据库和真实设备前，需要用最小依赖证明模拟 Driver、
Event Bus 与数字孪生可以通过正式契约形成可重复数据链。开发服务器当前只有
Python 3.10 宿主环境，正式项目基线仍为 Python 3.11+。

## 决策

1. Phase 2 使用有界 `InMemoryEventBus` 作为开发/SIL Adapter，发布只确认进入
   所有匹配消费者的有界队列；处理完成通过显式 drain barrier 确认。
2. 使用注入的 `SimClock` 和按“主种子+组件路径”派生的独立随机流，不读取业务墙钟，
   新增无关组件不得改变既有传感器随机序列。
3. 雷达模拟器实现正式 `SensorDriver` 端口，输出 `sensor/1.0` 和
   `health/1.0`，不向业务模块发布 truth。
4. Twin 采用单写者内存 Store，revision 每个成功事件增加一次；重复、晚到和容量
   溢出均有显式结果，快照包含固定 revision 的实体引用和因果输入。
5. `sensor.normalized.*` 在 Phase 2 仅形成 Sensor Evidence，不形成航迹或目标。

## 备选方案

### 立即部署外部 Broker 和数据库

暂不采用。会把 Phase 2 的确定性和契约问题与基础设施部署问题耦合；外部实现仍需
在后续作为相同端口的 Adapter 加入并运行共享契约测试。

### Twin 直接读取模拟 truth

拒绝。会产生测试泄漏，使后续感知和风险结果失真，并违反生产模块不能订阅 truth
的安全边界。

### 使用全局随机数

拒绝。组件增删或执行顺序变化会改变全部输出，无法满足固定种子回放一致性。

## 兼容影响

- 新增 `health/1.0` 和 `twin.snapshot/1.0` 首版契约及 protobuf，不改变六类核心
  v1 消息。
- 通用 Envelope 的 Schema 名规则允许点分层名称，以支持已登记的
  `twin.snapshot/1.0`。
- 内存 Event Bus 是 Adapter，不改变 `EventBusPort`；后续 Broker 可替换。

## 迁移计划

Phase 3 由 `perception.tracks` 更新动态目标；Phase 2 的 Sensor Evidence 保留作
数据新鲜度和来源证据。引入持久化 Twin Repository 时，用相同 reducer/outcome
语义增加 checkpoint、事件重建和 outbox。

## 回滚

删除 Phase 2 组合根并切换其他 EventBus/Twin Repository Adapter 即可。消息契约
若已经产生数据则不得复用字段编号；需按接口版本规则迁移。
