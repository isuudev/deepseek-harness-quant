# 自动交易 TODO（预留入口，当前未实现）

当前系统没有自动下单能力。`/api/decide` 和 `/api/portfolio/sell` 只写入本地决策/持仓状态，
不会向券商发送委托。

未来接入真实执行时应按以下顺序实现：

1. 券商适配器：QMT / miniQMT / Ptrade / 券商官方 API
2. 独立进程与凭据隔离
3. dry-run 与仿真账户验收
4. 订单幂等、撤单、超时和成交回报
5. 资金/持仓对账
6. 全局 Kill Switch
7. 与 L0、数据审计、单票上限、行业上限联动
8. 人工授权开关，默认关闭

当前仅提供：

- `config/execution.yaml.example`
- `execution/broker_gateway.py`
- `GET /api/execution/status`

所有真实委托调用保持 `NotImplementedError`，不会误下单。
