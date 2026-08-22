# 审计日志保留期清理（AUDIT_LOG_RETENTION_DAYS 落地）

> 日期：2026-08-22
> 状态：已完成（+3 tests，186 passed；uvicorn 冒烟启动正常）
> 来源：spec `2026-06-04-gateflow-mvp-design.md` §841/§870/§1337（v0.2.0 承诺）；
> 2026-08-22 查证发现 config 字段已存在但无任何实现（死配置）
> 目标：让 `AUDIT_LOG_RETENTION_DAYS`（默认 90）真正生效，兑现合规承诺

## 背景

- `config.py` 的 `AUDIT_LOG_RETENTION_DAYS: int = 90` 与 `.env.example`、spec 均承诺
  "超过 N 天的日志由后台任务清理"，但仓库中无对应任务--用户设置该值无任何效果
- spec §1337 明确动机：GDPR/PIPL 合规要求"按需最小化保留"（审计行含
  username/department/请求预览）
- 不清理的副作用：audit_logs 无限膨胀（每次 LLM 调用一行 + Fernet 加密 body），
  用量统计聚合越来越慢

## 方案决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 删除 vs 归档 | 直接 DELETE | 备份功能已提供 pg_dump（可含 audit_logs），要历史的先备份；归档表是过度设计 |
| 任务载体 | 并入 P2-6 的清理循环，改名 `audit_maintenance_loop` | 同域低频任务（24h 一轮），一个后台任务做两件事；旧名 `stale_pending_cleanup_loop` 语义偏窄 |
| 大表删除 | 按 id 子查询分批（默认 1000/批） | 单条大 DELETE 长事务锁表；PG/SQLite 都兼容 `id IN (SELECT ... LIMIT n)` |
| `RETENTION_DAYS <= 0` | 永久保留（跳过删除） | 常见语义；写进 config 注释 |
| 指标 | `gateflow_audit_log_deleted_total` Counter | 删除量是运维关心的数字（P2-8 刚建好指标体系，顺手补） |

**已知 trade-off（写入 CHANGELOG）**：用量统计基于 AuditLog 实时聚合，
保留期外的日志删除后统计窗口同步缩短到保留期--这本来就是保留期的语义，
但需要在文档里明说，避免"为什么 90 天前的用量不见了"的疑问。

## 任务分解

1. `cleanup_service.py`：`delete_expired_audit_logs()`（分批删 + 指标）+ 循环改名
   `audit_maintenance_loop` 并接入 retention 步骤
2. `metrics.py`：`gateflow_audit_log_deleted_total` Counter
3. `main.py`：import 与 create_task 同步改名
4. `config.py` / `.env.example` 注释：标明已实现 + `<=0` 语义
5. 测试：过期删除 / 新日志保留 / `0` 禁用 / 分批循环 / 指标增量
6. CHANGELOG + spec §841 状态更新 + commit

## 验收标准

1. 91 天前的日志被删、新日志保留；`RETENTION_DAYS=0` 时全保留
2. 超过一批的删除分多轮完成（batch_size 参数化验证）
3. `gateflow_audit_log_deleted_total` 反映删除量
4. 全套测试通过，ruff 无新警告；uvicorn 冒烟启动正常
