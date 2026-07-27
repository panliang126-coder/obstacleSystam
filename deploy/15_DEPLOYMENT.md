# 部署与运维设计

## 1. 模块目标

定义开发服务器、边缘计算设备、GPU/NPU 节点和 HIL/LIVE 环境的可复现部署、配置、Secret、健康检查、升级、回滚、日志、备份和安全边界。

## 2. 部署形态

### 2.1 单机开发/SIL

- Python 虚拟环境或容器；
- InProcess Event Bus；
- SQLite、本地 artifact 目录；
- 模拟器和业务服务可合并；
- PyQt GUI 独立进程。

只允许 `SIM/REPLAY`，默认不安装真实 Control Adapter。

### 2.2 边缘一体机

```text
Edge Node
├── ingest-service (device access)
├── perception-service (GPU/NPU optional)
├── world-service (weather/twin/risk)
├── autonomy-service (planning/decision)
├── control-gateway (isolated privileges)
├── management-agent
└── local broker/WAL/cache
```

中心服务器负责长期存储、模型/配置、回放和运营 GUI；断开中心时边缘在策略允许范围内独立运行。

### 2.3 HIL/LIVE

- Control Gateway 独立服务账户/容器/主机权限；
- 网络 ACL 只允许 Safety Gate 和指定飞控端点；
- 物理急停、飞控原生 failsafe 和链路监控；
- 运行模式、vehicle ID、endpoint 和授权令牌绑定；
- 生产数据库/Broker/日志跨故障域按需求冗余。

## 3. 构建产物

每个服务产物必须：

- 锁定基础镜像 digest 和依赖 lockfile；
- 非 root 运行（设备访问用最小 group/capability）；
- 包含 SBOM、版本、commit/build ID；
- 通过漏洞/Secret/许可证扫描；
- 不内置密码、私钥、生产 endpoint；
- 对模型、插件、Schema 和迁移记录 hash。

镜像标签可读，但部署必须使用不可变 digest。

## 4. Docker/Compose 基线

开发 Compose 可含：

- `broker`
- `postgres`
- `influxdb`
- `object-store`
- `ingest`
- `perception`
- `world`
- `autonomy`
- `management`
- `gui-gateway`
- `simulator`

生产可使用 Compose/systemd/Kubernetes，但不得改变消息契约和安全边界。容器只挂载所需目录，Control Gateway 不与 GUI 共享设备 socket。

## 5. 配置与 Secret

目录建议：

```text
/etc/low-altitude-ai/
├── bootstrap.yaml          # 仅启动所需，非敏感
├── logging.yaml
└── trust/                  # 证书/签名公钥，严格权限

/var/lib/low-altitude-ai/
├── cache/
├── spool/
├── artifacts/
└── checkpoints/

/var/log/low-altitude-ai/   # 若不直接发集中日志
```

- 有效业务配置由 Config Service/签名配置包提供。
- Secret 由环境 Secret Store、系统凭据或只读挂载注入。
- 不允许 `.env` 带生产密码进入仓库/镜像。
- 文件权限：配置最小只读，私钥仅服务账户，日志不可含 Secret。
- 启动时打印配置 hash 和非敏感摘要，不打印原文凭据。

## 6. GPU/NPU

服务启动时执行 capability probe：

- 设备型号、驱动、运行时和可用内存；
- 模型 artifact 的架构/算子/精度兼容；
- 固定自检样例输出和延迟；
- 资源配额和并发。

GPU/NPU 不可用：

- 若存在经过验证的 CPU/备用插件，则状态 DEGRADED 并回退；
- 无安全能力时 readiness=false；
- 不能静默切换到未经测试的精度/模型。

## 7. 网络与端口

划分设备网、控制网、服务网、管理网：

- 设备 Adapter 仅访问指定传感器 IP/串口/CAN。
- Control Gateway 只接受 Safety Gate 身份，只有它访问飞控。
- Broker Topic ACL 与服务身份绑定。
- 管理 API 通过 TLS、鉴权和审计；数据库不暴露到操作员网。
- 外部天气/地图经 egress allowlist，断网有明确降级。

端口清单由部署 profile 生成并纳入防火墙测试，本文不硬编码未经环境确认的端口。

## 8. 服务启动与停止

启动顺序：

```text
storage/broker/object-store
  -> management/config/schema registry
  -> event bus adapters
  -> ingest/simulator
  -> perception/weather/twin/risk
  -> planning/decision/safety gate
  -> control gateway (authorization disabled initially)
  -> GUI/API
```

readiness 依赖能力而非固定 sleep。Control Gateway 启动不等于取得 LIVE 权限。

优雅停止：

1. 撤销新任务/控制授权；
2. 排空安全关键事件和 Outbox；
3. 保存 Twin checkpoint/run manifest；
4. 停插件/驱动；
5. 关闭 Event Bus/存储。

超时后强制停止必须形成 incomplete run 记录。

## 9. 健康检查

| 检查 | 含义 |
|---|---|
| liveness | 进程 event loop/主线程仍响应 |
| readiness | 配置/Schema/插件/关键依赖和数据新鲜度可用 |
| startup | 模型加载/数据库迁移等慢启动完成 |
| capability | 感知、风险、规划、控制等能力级健康 |

不要因数据库指标写入失败把 Control Gateway 立即 kill；由能力依赖和降级策略决定。

## 10. 数据库迁移

- CI 用生产同版本数据库执行迁移和回滚/前滚测试。
- 部署先执行兼容性 expand migration，再部署双读写应用。
- 破坏性 contract migration 在旧版本退役后单独执行。
- 迁移前备份、容量检查；迁移 job 与应用账户分离。
- 应用启动不自动获得 DDL 超级权限。

## 11. 发布策略

### 11.1 服务

滚动或 blue/green：

1. 部署新实例但不接安全关键流量；
2. readiness/自检；
3. 影子消费并比较指标；
4. 小比例/指定 vehicle 切换；
5. 观察；
6. 扩大或回滚。

### 11.2 插件/模型

遵守 Management 的 `STAGED -> SHADOW -> ACTIVE -> DRAINING`。学习模型切换需比较精度、延迟、风险/决策差异和资源。

### 11.3 Control Gateway

不做未经 HIL 验证的自动灰度。升级前撤销 LIVE 授权、确认飞控进入安全模式，升级后重新执行连接/单位/frame/Ack 自检和人工授权。

## 12. 回滚

回滚包包括：

- 前一镜像 digest；
- 前一配置/插件/模型 hash；
- Schema/数据库兼容说明；
- checkpoint 兼容；
- 回滚触发阈值和负责人。

数据库优先前滚修复；若确需回滚，必须证明新写数据可被旧应用读取或先执行数据转换。回滚本身写审计和 run manifest。

## 13. 日志、指标与存储

- stdout 结构化日志由采集器发送；边缘有有界 spool。
- Risk/Decision/Control/Audit 不采样；高频 DEBUG/原始帧按策略采样。
- Metrics、Trace 和事件关联 `run_id/trace_id/vehicle_id`。
- 容量告警阈值：磁盘、对象存储、数据库、spool、WAL、Broker backlog。
- 轮转/保留遵守 `04_DATABASE_DESIGN.md`，不得由容器无限写本地层。

## 14. 备份与灾难恢复

- 备份 PostgreSQL、InfluxDB 必要 bucket、对象 artifact、配置/注册表和证书元数据。
- 密钥材料按 Secret 系统流程单独备份/轮换。
- 每季度执行恢复演练，验证 artifact/config hash 和回放。
- 边缘离线缓存恢复上传按 event ID 幂等，不能产生重复 Decision/Control 记录。

## 15. 安全加固

- 最小基础镜像、只读 rootfs（能实现时）、drop capabilities、seccomp/AppArmor。
- 主机设备映射 allowlist；不使用 `--privileged` 作为常规方案。
- mTLS/证书轮换、服务身份、Topic/API ACL。
- 构建签名、部署准入和 artifact 签名验证。
- NTP/PTP 健康监控；时间异常影响 readiness/风险。
- 远程维护操作有 MFA/审计/时限，不开放共享账户。

## 16. 运维 Runbook 最小集合

- 单传感器/全部感知离线；
- Broker/数据库/对象存储不可用；
- GPU OOM/模型加载失败；
- 控制链路中断/飞控拒绝命令；
- 时钟同步失效；
- 磁盘/spool 满；
- 插件回滚；
- 配置错误回滚；
- 现场急停和安全着陆；
- 备份恢复和事件导出。

每份 Runbook 含症状、告警、立即安全动作、诊断、恢复、验证和升级路径。

## 17. 验收标准

1. 从空主机依据版本清单可重复部署，所有 artifact hash 一致。
2. CI/SIM 环境无法访问真实 Control endpoint。
3. 服务以最小权限运行，镜像不含 Secret/高危未处理漏洞。
4. 单服务滚动升级不丢 Risk/Decision/Control 审计。
5. 插件/配置/服务升级失败可按目标时间回滚。
6. 断中心连接时边缘按设计继续/降级，恢复后幂等补传。
7. 备份恢复满足 `04_DATABASE_DESIGN.md` RPO/RTO 基线。
8. 24 小时标称负载无无界磁盘、内存、线程或 backlog 增长。

## 18. 测试方法

- 构建复现、SBOM、签名、漏洞、Secret 和镜像用户检查。
- Compose/目标编排环境端到端 smoke。
- 网络 ACL/Topic ACL/RBAC/证书过期与轮换测试。
- 服务 kill、节点重启、网络分区、磁盘满和依赖失效。
- 滚动/blue-green、插件/模型、配置和 Control Gateway 回滚演练。
- GPU/NPU 缺失、驱动不兼容、OOM、CPU 回退。
- 备份恢复后运行固定 REPLAY，比较关键输出。
