## Market Lessons

- 初始化记忆库：记录与市场结构相关的稳定经验，避免写入具体价格预测。

## Architecture Facts

- 2026-05-06：完成 Harness V2 改造。信号链路新增三层校验（schema/sanity/policy），LLM 预算管理与自动降级，全链路 trace（SQLite+FTS5）。
- `risk/risk_manager.py` 已改为兼容层，实际逻辑在 `harness/verification/policy_gate.py`。
- `core/state_store.py` 新增 `core/state/` 子模块（market_state / position_state / stats），持仓状态落盘到 SQLite。
- `update_position(None)` 无 symbol 参数会兜底清空所有持仓；正常路径应传 `symbol=` 仅清除指定标的。
- Telegram HITL 审批门（`harness/hitl/`）当前为桩实现，`enabled=False`。
- `evolution/` 模块（postmortem / skill_health / skill_lifecycle / walk_forward / judge）已落地但属于"可继续增强"状态。
- 新增环境变量：`LLM_DAILY_TOKEN_LIMIT`（默认 200000）、`LLM_PER_CALL_TOKEN_LIMIT`（默认 4000）。
