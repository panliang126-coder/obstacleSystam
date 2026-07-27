# ADR 0006: Phase 6 可无界面测试的 PyQt MVVM 边界

- 状态：Accepted
- 日期：2026-07-27

## 背景

GUI 必须处理高频航迹、Risk/Decision/Ack、断线和回放，同时不能导入业务插件、
私有数据库或 Control Gateway。测试服务器没有桌面会话，但仍必须执行真实 Qt
模型、Signal 和主线程交互测试。

## 决策

1. `UiStateStore` 和 `ReplayController` 使用纯 Python 不可变 DTO，负责实体合并、
   sequence 缺口、数据年龄、连接状态、有界历史和确定性 seek/step。
2. PyQt6 只位于 `ui.qt` Adapter。后台生产的 Snapshot 通过 queued Signal 进入
   主线程，View 不计算 Risk、Path 或 Decision。
3. `UiCommandClient` 在断线、同步、降级和 REPLAY 状态本地拒绝命令；服务端
   `ManagementService` 再执行 RBAC、revision、幂等和审计校验。
4. 配置与插件使用显式状态机；安全配置需要两个不同 Safety Approver。Health
   心跳超时显示 UNKNOWN，并将关键能力 UNKNOWN 提升为系统 UNHEALTHY。
5. PyQt6/pytest-qt 版本锁定，Qt 测试在 Linux 使用 `offscreen` 平台实际运行，
   不以 Mock QWidget 替代。

## 结果

- 断线旧数据不会继续显示为实时，控制按钮同步禁用。
- proposed、authorized 与 Ack 状态保持独立文本语义。
- 回放不会调用 LIVE 命令接口，同一 seek 可重建相同状态哈希。
- 配置、插件、健康和告警确认均有可测试 View/服务端审计路径。

## 局限

当前 Situation 页面是高性能表格投影，尚未接入地图瓦片、3D Scene Adapter、
远程 Query/WebSocket 和身份提供方。真实网络重连、视觉 golden、多 DPI 和
24 小时 GUI 稳定性仍需后续集成环境验证。
