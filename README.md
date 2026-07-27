# obstacleSystam

低空智能感知与自主决策系统（Low Altitude Intelligent Perception and Decision System）。

项目以数字孪生和模拟器建立可重复的“感知—环境—风险—规划—决策—控制”闭环，再通过与模拟驱动相同的端口接入 PX4/ArduPilot、雷达、EO/IR、气象、GNSS/IMU 和通信链路。

## 当前状态

当前里程碑为 Phase 2：模拟器与数字孪生。

- 17 份工程设计文档；
- 版本化事件信封和 UUIDv7；
- Event Bus、Driver、Plugin、Repository、Clock 端口；
- 六类核心及 Health/Twin Snapshot JSON Schema 和 protobuf 定义；
- 有效/无效契约样例；
- 有界内存 Event Bus、可控 SimClock 和确定性雷达模拟 Driver；
- 单调 revision、去重、晚到保护和容量限制的 Twin State Store；
- 可重复的“模拟雷达→Event Bus→Twin Snapshot”端到端链路；
- Schema、protobuf、架构、驱动、Event Bus、Twin 和集成测试。

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
