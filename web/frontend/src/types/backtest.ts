export type BacktestStatus = "queued" | "running" | "done" | "failed";

export interface LaunchRequest {
  strategy: string;
  start: string;
  end: string;
  symbol: string;
  llm_mode: "replay" | "rerun";
  initial_balance: number;
  timeframe: string;
  output_dir?: string;
}

export interface RunSummary {
  run_id: string;
  status: BacktestStatus;
  progress?: number;
  index?: number;
  total?: number;
  equity?: number;
  trades?: number;
  strategy_name: string;
  symbol: string;
  start: string;
  end: string;
  started_at?: string;
  updated_at?: string;
  finished_at?: string;
  error?: string | null;
  win_rate?: number;
  sharpe?: number;
  max_drawdown?: number;
}

export interface BacktestMeta {
  strategies: string[];
  default_symbol: string;
  default_timeframe: string;
  llm_modes: ("replay" | "rerun")[];
  timeframe_options: string[];
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface TradeItem {
  trade_id: string;
  symbol: string;
  side: string;
  amount: number;
  price: number;
  pnl: number;
  fee: number;
  timestamp: string;
}

export interface BacktestResult {
  run_id: string;
  config: Record<string, unknown>;
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  max_drawdown: number;
  equity_curve: EquityPoint[];
  trades: TradeItem[];
  metrics_by_regime: Record<string, unknown>;
}

export interface WsEvent<T = unknown> {
  type: string;
  data?: T;
}
