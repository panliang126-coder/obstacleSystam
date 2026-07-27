# obstacleSystam

低空智能感知与自主决策系统（Low Altitude Intelligent Perception and Decision System）。

项目以数字孪生和模拟器建立可重复的“感知—环境—风险—规划—决策—控制”闭环，再通过与模拟驱动相同的端口接入 PX4/ArduPilot、雷达、EO/IR、气象、GNSS/IMU 和通信链路。

## 当前状态

当前里程碑为 Phase 6：GUI 与系统管理。

- 17 份工程设计文档；
- 版本化事件信封和 UUIDv7；
- Event Bus、Driver、Plugin、Repository、Clock 端口；
- 六类核心及 Vehicle/Mission/Control/Health/Twin JSON Schema 和 protobuf 定义；
- 有效/无效契约样例；
- 有界内存 Event Bus、可控 SimClock 和确定性雷达模拟 Driver；
- 单调 revision、去重、晚到保护和容量限制的 Twin State Store；
- 可重复的“模拟雷达→Event Bus→Twin Snapshot”端到端链路；
- 不读取模拟 truth 的雷达检测关联、航迹确认/滑行/丢失基线；
- 带物理质量控制、加权融合和保守缺测语义的天气估计；
- 可重复的“Sensor→Perception/Weather→Twin”Phase 3 链路；
- 基于 CPA/TCPA、天气、能源、链路和 Twin 新鲜度的解释性规则风险引擎；
- 带连续线段碰撞检查、地理围栏和无解拒绝的确定性绕行规划器；
- 可重复的“Twin→Risk→Path”Phase 4 动态交叉目标 SIL 链路；
- 带动作优先级、输入去重和恢复滞回的确定性安全状态机；
- 独立 fail-closed Safety Gate 和仅允许 SIM 的幂等模拟 Control Gateway；
- Continue/Avoid/Return/Land/Hold 全场景 Decision→Command→Ack 追溯链；
- 有界 UI State Store、sequence 缺口检测、数据年龄与断线冻结语义；
- 确定性 REPLAY pause/seek/step，回放状态下强制禁用 LIVE 命令；
- PyQt6 Overview/Situation/Risk & Decision/Health/Replay/Management 工作区；
- 带 RBAC、幂等、revision、双人安全审批和不可变审计的管理服务；
- 配置、插件、健康聚合和告警确认视图；
- Schema、protobuf、架构、驱动、插件、性能和集成测试。

系统行为和开发顺序以 [00_MASTER.md](00_MASTER.md) 为准。

## 使用 Python 3.11

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
obstacle-schema validate-examples
pytest
```

运行 Phase 2 的确定性演示：

```bash
obstacle-phase2-demo
```

命令会执行 10 个模拟雷达扫描，输出规范化事件哈希、最终 Twin revision
和快照哈希。同一场景、种子和版本的结果必须完全一致。

运行 Phase 3 感知与天气演示：

```bash
obstacle-phase3-demo
```

该命令输出航迹确认数、位置 RMSE、天气风速误差、Twin revision 和三条确定性
哈希。基线场景要求位置 RMSE ≤ 2 m、风速误差 ≤ 2 m/s。

运行 Phase 4 风险与路径规划演示：

```bash
obstacle-phase4-demo
```

标准动态交叉场景必须输出结构化 `CLOSING_TRACK` 解释、HIGH/CRITICAL 风险以及
通过连续碰撞验证且最小净空不低于配置的绕行路径。

运行 Phase 5 决策闭环演示：

```bash
obstacle-phase5-demo
```

该命令执行 Continue、Avoid、Return、Land、Hold 五个确定性 SIL 场景。每个场景
必须通过独立 Safety Gate，生成可追溯的模拟控制命令和 Ack；重复发送不能产生重复
副作用，真实 endpoint 命令数必须为 0。

启动 Phase 6 PyQt6 前端：

```bash
obstacle-gui
```

无桌面测试环境可使用 `obstacle-gui --offscreen --smoke-test` 验证启动。GUI 只消费公共 Snapshot/Query/
Command DTO；它不会直接连接业务插件、数据库或飞控。断线、同步、降级和回放状态
都会禁用控制类命令，服务端仍会再次执行 RBAC 和安全校验。

服务器宿主机没有 Python 3.11 时，可使用 Docker：

```bash
docker build --target test -t obstacle-system:test .
```

## 目录

```text
src/low_altitude_ai/   领域、端口、Adapter、模拟器和数字孪生
configs/               场景 Schema 与确定性场景
schemas/v1/            JSON Schema 2020-12
schemas/examples/      有效/无效契约样例
proto/                 protobuf v1 定义
tests/                 单元、契约和架构测试
modules/               各业务模块设计
frontend/              PyQt 前端设计
deploy/                部署设计
codex/                 Codex Agent 规范
```

## 安全说明

当前阶段不会连接真实飞控。`SIM`/`REPLAY` 实现必须与真实 Control Gateway 物理和权限隔离；任何 LIVE 能力都需经过 SIL、HIL、安全评审和显式授权。
