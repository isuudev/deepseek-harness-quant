# 更新日志

## v1.0.10（开发中，2026-09-10）

- 今日信号开源重实现：`report/daily_signal.py`（L2 决策卡同源聚合，替代原外包包，补齐决策链「今日信号」环节）
- 配置模板：`daily_signal` 段（params.yaml / params.yaml.example），改参数不改代码

## v1.0.9（2026-08-16）

- 牛散量化决策台（6 位牛散人格 + 全量数据快照）
- 排名引擎量价四强升级
- 控制页会话管理（筛选/删除/恢复）
- 同源代理稳定性
- ETF 嵌入排版修复

## v1.0.0（2026-08-16）

首个开源发布版本基线（代码 + HARNESS 运行时 + 演示数据 + 更新机制）。

- 全功能：Pitch 决策链 / 因子引擎 / 五池远期验证（含牛散主观）/ ETF 映射 / 全站 UI
- HARNESS 深度嵌入（桥接插件 + 7 空白牛散 skill + API Key 接入点）
- 动态化铁律落地：回测策略外部注册（config/strategies.yaml）、ETF 候选池配置化（config/etf_pool.yaml）
- 回测验收标准 skill（backtest-acceptance）
- 更新机制：scripts/update.py（manifest 驱动覆盖，用户配置/数据保护，应用前自动备份）

更新方式：见 docs/更新与发布.md
