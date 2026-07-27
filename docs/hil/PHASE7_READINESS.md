# Phase 7 HIL readiness

检查时间：2026-07-27

检查主机：`ecs-55639127`

## 当前结论

`NOT READY`。未连接、未探测、未向任何真实控制 endpoint 发送命令。

服务器只发现 QEMU USB Tablet；未发现：

- `/dev/ttyACM*`、`/dev/ttyUSB*` 或 `/dev/serial/by-id/*`；
- PX4/ArduPilot 飞控台架；
- ROS2；
- MAVLink/雷达/相机录制流；
- 急停、无桨、隔离网络和飞控原生 failsafe 的验证记录；
- HIL 操作员与独立安全审批人；
- 固件、标定、接线、回滚方案和测试 run manifest。

## 已完成的软件准备

- `FlightControllerPort` 和 Transport 注入边界；
- 只允许 `HIL` 的证据 Permit；
- endpoint、硬件、固件/标定哈希、时效和双人审批检查；
- ENU→NED 映射；
- 控制命令幂等和 `control.ack/1.0` 契约；
- Fake MAVLink Transport 共享契约测试。

## 进入真实 HIL 前必须提供

1. 明确的 PX4 或 ArduPilot 型号、固件版本/hash 和连接方式；
2. 物理台架位置、无桨/执行器隔离、急停与负责人；
3. 只允许指定 endpoint 的隔离网络或串口 by-id；
4. 飞控原生 failsafe、模式、armed、NED 单位和 Ack 映射表；
5. 标定 hash、接线图、供电和时钟同步证据；
6. 故障注入计划、回滚包、go/no-go 和独立安全审批；
7. 真实硬件上执行 Driver/Control Contract 与 HIL 场景的批准。

在这些条件满足前，不安装会自动发现设备的 Transport，不启用 LIVE，也不把
Fake/录制流测试报告为真实 HIL 结果。
