# 安全策略

## 报告漏洞

如果你发现了安全漏洞，**请不要在公开 issue 中披露**。

请通过 [GitHub Security Advisories](https://github.com/ai-dev-dot/gateflow/security/advisories/new) 私下报告。

## 报告内容

请尽可能提供：

- 漏洞描述与影响范围
- 复现步骤（最小可复现 demo）
- 受影响版本（commit hash 或 tag）
- 建议的修复方案（如有）
- 你的姓名 / 联系方式（用于跟进）

## 响应时间

这是一个**个人维护项目**，无 SLA 承诺。维护者会在业余时间处理：
- 确认收到：尽力 48 小时内
- 高危漏洞：会优先处理并尽快发布
- 中低危：随版本节奏

## 支持的版本

仅最新 `main` 分支接收安全更新。已发布 tag 的旧版本不再维护。

## 已知安全考量

- **API Key 哈希存储**：客户端 API Key 在数据库中以 HMAC-SHA256 哈希存储，明文只在创建时返回一次
- **Provider API Key 加密存储**：上游 API Key 用 Fernet 对称加密落库（`encrypted_key` + `key_preview`），运行时通过 `get_decrypted_key()` 解密
- **JWT 有效期**：默认 7 天，可在管理后台调整
- **审计日志受控变更**：调用方不可篡改记录；记录仅由系统自身变更--请求完成路径补写失败原因（`error_message`）、后台任务把僵尸 pending 标记为 failed、按 `AUDIT_LOG_RETENTION_DAYS` 删除过期日志（删除无行级痕迹，仅服务日志与 `gateflow_audit_log_deleted_total` 指标可查）
- **`/metrics` 端点无认证**：默认暴露（`METRICS_ENABLED` 可关），包含 HTTP 路径模板、状态码分布、延迟与 LLM 调用量等运维指标。内网部署可接受；公网部署必须置于带认证的反向代理之后
- **审计日志 body 加密**：完整 `request_body` 用 Fernet 加密，列表接口不返回；详情接口默认不含 body，`?include_body=true` 仅 admin 可用且每次访问写 meta-audit
