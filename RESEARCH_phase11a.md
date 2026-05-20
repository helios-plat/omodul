# Phase 11A Research — Agent + Scheduler 技术调研

调研日期: 2026-05-20

## 1. APScheduler 4.x 状态

**结论: 使用 APScheduler 3.10.4**

APScheduler 4.x 目前 (2026-05) 需要显式配置数据存储 (data store) 和事件 broker，
且 API 与规格书中的代码 (`AsyncIOScheduler`, `CronTrigger.from_crontab`) 不兼容。
3.10.x 稳定支持 asyncio (`AsyncIOScheduler`) + cron trigger，满足 Stratum 需求。

已安装: apscheduler==3.10.4

## 2. Cron 表达式精度

**结论: 标准 UNIX 5 字段 + timezone (Asia/Shanghai)**

`CronTrigger.from_crontab(expr, timezone="Asia/Shanghai")` 支持标准 5 字段 cron
（分 时 日 月 周），满足 daily_inbox / daily_digest / weekly_lint / nightly_translation 需求。
Quartz 7 字段 (秒级) 对 Stratum 过度，不采用。

## 3. MCP Python SDK 客户端模式

**结论: Phase 11A 实施 stdio transport (框架); SSE/WebSocket 留 Phase 11B**

MCP SDK 已在 omodul venv 中可用。`mcp.ClientSession` + `mcp.stdio_client` 支持进程内
stdio transport，适合本地工具服务器 (whisper / TTS 等本地进程)。
SSE 和 WebSocket transport 留给 Phase 11B 网络外挂场景。

## 4. Retry 策略

**结论: 自实施 (spec RetryPolicy 约 60 行)**

自实施指数退避 + jitter，与 `oprim._logging` 无缝集成，不引入 tenacity 依赖。

## 5. Circuit Breaker

**结论: 自实施 (~150 行)**

三状态 (CLOSED / OPEN / HALF_OPEN) 自实施，简单可控，避免引入 pybreaker 外部依赖。

## 6. Job Lock

**结论: Redis 7.4.0 (redis.asyncio)**

Phase 2 已有 Redis container，复用。`redis.asyncio` 分布式锁防多 Scheduler 实例
重复执行同一 job。已安装: redis==7.4.0。

## 依赖变更汇总

### omodul pyproject.toml 新增 deps:
- `apscheduler>=3.10,<4.0`
- `redis>=7.0`

### oprim pyproject.toml 新增 deps (oprim.external):
- `redis>=7.0` (run_lock 用)
- 其他已有: `httpx>=0.27`, `mcp>=1.0`
