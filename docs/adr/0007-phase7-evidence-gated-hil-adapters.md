# ADR 0007: Phase 7 HIL 适配器必须由证据许可门控

- 状态：Accepted
- 日期：2026-07-27

## 背景

服务器当前没有 PX4/ArduPilot、串口/CAN 设备、ROS2 或厂商录制流。真实控制适配器
若只靠配置开关启用，会把软件测试误当成物理安全证据。

## 决策

1. HIL 连接前必须提交 endpoint/hardware/firmware/calibration 哈希、急停、无桨、
   隔离网络、飞控原生 failsafe、回滚方案、时钟同步和独立审批证据。
2. `HilReadinessGate` 只签发短时 HIL Permit，永远不签发 LIVE 授权。
3. MAVLink Adapter 不自行发现或打开串口/网络；厂商 Transport 必须在台架批准后
   注入，并绑定 Permit、endpoint、deadline 和授权哈希。
4. ENU→NED 只在飞控 Adapter 边界执行。共享契约测试验证坐标、时效、模式、
   endpoint、幂等和 Ack Schema。
5. 没有真实硬件、故障注入、安全评审和回滚演练时，Phase 7 保持未完成状态。

## 结果

可以提前验证不依赖硬件的控制边界，同时不会把 Fake Transport 结果标记为 HIL
通过。接入具体 PX4/ArduPilot Transport 需要新的明确设备范围和台架授权。
