CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS accounts (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    cash_balance NUMERIC(20, 6) NOT NULL DEFAULT 100000,
    starting_cash NUMERIC(20, 6) NOT NULL DEFAULT 100000,
    base_currency TEXT NOT NULL DEFAULT 'USD',
    active_symbol TEXT NOT NULL DEFAULT 'AAPL',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watchlist (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);

CREATE TABLE IF NOT EXISTS strategy_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    ma_fast INTEGER NOT NULL DEFAULT 5,
    ma_slow INTEGER NOT NULL DEFAULT 20,
    macd_fast INTEGER NOT NULL DEFAULT 12,
    macd_slow INTEGER NOT NULL DEFAULT 26,
    macd_signal INTEGER NOT NULL DEFAULT 9,
    rsi_period INTEGER NOT NULL DEFAULT 14,
    rsi_buy_threshold NUMERIC(10, 4) NOT NULL DEFAULT 30,
    rsi_sell_threshold NUMERIC(10, 4) NOT NULL DEFAULT 70,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS ma_fast INTEGER NOT NULL DEFAULT 5;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS ma_slow INTEGER NOT NULL DEFAULT 20;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS macd_fast INTEGER NOT NULL DEFAULT 12;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS macd_slow INTEGER NOT NULL DEFAULT 26;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS macd_signal INTEGER NOT NULL DEFAULT 9;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS rsi_period INTEGER NOT NULL DEFAULT 14;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS rsi_buy_threshold NUMERIC(10, 4) NOT NULL DEFAULT 30;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS rsi_sell_threshold NUMERIC(10, 4) NOT NULL DEFAULT 70;
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE strategy_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS positions (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    qty NUMERIC(20, 8) NOT NULL CHECK (qty >= 0),
    avg_price NUMERIC(20, 8) NOT NULL CHECK (avg_price >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    market TEXT NOT NULL DEFAULT '',
    strategy_name TEXT,
    signal_source TEXT,
    entry_reason TEXT,
    entry_price NUMERIC(20, 8),
    highest_price NUMERIC(20, 8),
    stop_loss_pct NUMERIC(10, 4),
    take_profit_pct NUMERIC(10, 4),
    trailing_stop_pct NUMERIC(10, 4),
    max_holding_days INTEGER,
    timeframe TEXT,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);

ALTER TABLE positions ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE positions ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS strategy_name TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS signal_source TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS entry_reason TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS entry_price NUMERIC(20, 8);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS highest_price NUMERIC(20, 8);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS stop_loss_pct NUMERIC(10, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS take_profit_pct NUMERIC(10, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS trailing_stop_pct NUMERIC(10, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS max_holding_days INTEGER;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS timeframe TEXT;

CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL DEFAULT 'market',
    status TEXT NOT NULL CHECK (status IN ('pending', 'filled', 'cancelled', 'rejected')),
    qty NUMERIC(20, 8) NOT NULL CHECK (qty > 0),
    filled_qty NUMERIC(20, 8) NOT NULL DEFAULT 0 CHECK (filled_qty >= 0),
    price NUMERIC(20, 8),
    value NUMERIC(20, 8),
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id BIGINT REFERENCES orders(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty NUMERIC(20, 8) NOT NULL CHECK (qty > 0),
    price NUMERIC(20, 8) NOT NULL CHECK (price >= 0),
    value NUMERIC(20, 8) NOT NULL CHECK (value >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    account_balance_after NUMERIC(20, 8),
    position_qty_after NUMERIC(20, 8),
    realized_pnl NUMERIC(20, 8),
    equity_before NUMERIC(20, 8),
    equity_after NUMERIC(20, 8),
    equity_change NUMERIC(20, 8),
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE trades ADD COLUMN IF NOT EXISTS account_balance_after NUMERIC(20, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS position_qty_after NUMERIC(20, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(20, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS equity_before NUMERIC(20, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS equity_after NUMERIC(20, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS equity_change NUMERIC(20, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS execution_source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy_name TEXT;

CREATE TABLE IF NOT EXISTS auto_trading_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT false,
    stopped BOOLEAN NOT NULL DEFAULT false,
    stop_reason TEXT NOT NULL DEFAULT '',
    position_pct NUMERIC(10, 4) NOT NULL DEFAULT 10,
    max_positions INTEGER NOT NULL DEFAULT 3,
    max_daily_loss_pct NUMERIC(10, 4) NOT NULL DEFAULT 5,
    max_total_drawdown_pct NUMERIC(10, 4) NOT NULL DEFAULT 20,
    cooldown_hours NUMERIC(10, 4) NOT NULL DEFAULT 6,
    allow_add_position BOOLEAN NOT NULL DEFAULT false,
    scan_scope TEXT NOT NULL DEFAULT 'mixed',
    signal_mode TEXT NOT NULL DEFAULT 'best',
    timeframe TEXT NOT NULL DEFAULT '1h',
    scan_interval_minutes INTEGER NOT NULL DEFAULT 15,
    stop_loss_pct NUMERIC(10, 4) NOT NULL DEFAULT 5,
    take_profit_pct NUMERIC(10, 4) NOT NULL DEFAULT 15,
    trailing_stop_pct NUMERIC(10, 4) NOT NULL DEFAULT 8,
    max_holding_days INTEGER NOT NULL DEFAULT 30,
    max_portfolio_exposure_pct NUMERIC(10, 4) NOT NULL DEFAULT 70,
    max_crypto_exposure_pct NUMERIC(10, 4) NOT NULL DEFAULT 20,
    max_daily_orders INTEGER NOT NULL DEFAULT 10,
    kill_switch BOOLEAN NOT NULL DEFAULT false,
    quality_mode TEXT NOT NULL DEFAULT 'normal',
    quality_min_score NUMERIC(10, 4) NOT NULL DEFAULT 0,
    quality_min_sharpe NUMERIC(10, 4) NOT NULL DEFAULT -0.5,
    quality_min_return_pct NUMERIC(10, 4) NOT NULL DEFAULT -10,
    quality_max_drawdown_pct NUMERIC(10, 4) NOT NULL DEFAULT 50,
    quality_min_trade_count INTEGER NOT NULL DEFAULT 3,
    signals_generated INTEGER NOT NULL DEFAULT 0,
    signals_passed_filter INTEGER NOT NULL DEFAULT 0,
    signals_executed INTEGER NOT NULL DEFAULT 0,
    signals_rejected INTEGER NOT NULL DEFAULT 0,
    scheduler_status TEXT NOT NULL DEFAULT 'idle',
    last_executed_signal TEXT NOT NULL DEFAULT '',
    enabled_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS max_total_drawdown_pct NUMERIC(10, 4) NOT NULL DEFAULT 20;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS cooldown_hours NUMERIC(10, 4) NOT NULL DEFAULT 6;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS allow_add_position BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS scan_scope TEXT NOT NULL DEFAULT 'mixed';
ALTER TABLE auto_trading_settings ALTER COLUMN scan_scope SET DEFAULT 'mixed';
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS signal_mode TEXT NOT NULL DEFAULT 'best';
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS timeframe TEXT NOT NULL DEFAULT '1h';
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS scan_interval_minutes INTEGER NOT NULL DEFAULT 15;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS stop_loss_pct NUMERIC(10, 4) NOT NULL DEFAULT 5;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS take_profit_pct NUMERIC(10, 4) NOT NULL DEFAULT 15;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS trailing_stop_pct NUMERIC(10, 4) NOT NULL DEFAULT 8;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS max_holding_days INTEGER NOT NULL DEFAULT 30;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS max_portfolio_exposure_pct NUMERIC(10, 4) NOT NULL DEFAULT 70;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS max_crypto_exposure_pct NUMERIC(10, 4) NOT NULL DEFAULT 20;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS max_daily_orders INTEGER NOT NULL DEFAULT 10;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS kill_switch BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS quality_mode TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS quality_min_score NUMERIC(10, 4) NOT NULL DEFAULT 0;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS quality_min_sharpe NUMERIC(10, 4) NOT NULL DEFAULT -0.5;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS quality_min_return_pct NUMERIC(10, 4) NOT NULL DEFAULT -10;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS quality_max_drawdown_pct NUMERIC(10, 4) NOT NULL DEFAULT 50;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS quality_min_trade_count INTEGER NOT NULL DEFAULT 3;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS signals_generated INTEGER NOT NULL DEFAULT 0;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS signals_passed_filter INTEGER NOT NULL DEFAULT 0;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS signals_executed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS signals_rejected INTEGER NOT NULL DEFAULT 0;
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS scheduler_status TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE auto_trading_settings ADD COLUMN IF NOT EXISTS last_executed_signal TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS auto_trading_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT '',
    strategy TEXT NOT NULL DEFAULT '',
    signal TEXT NOT NULL DEFAULT '',
    score NUMERIC(10, 4) NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    price NUMERIC(20, 8),
    qty NUMERIC(20, 8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('deposit', 'withdrawal', 'adjustment')),
    amount NUMERIC(20, 6) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE account_transactions DROP CONSTRAINT IF EXISTS account_transactions_type_check;
ALTER TABLE account_transactions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'USD';
ALTER TABLE account_transactions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE account_transactions SET type = 'adjustment' WHERE type IS NULL OR type NOT IN ('deposit', 'withdrawal', 'adjustment');
ALTER TABLE account_transactions ADD CONSTRAINT account_transactions_type_check CHECK (type IN ('deposit', 'withdrawal', 'adjustment'));

CREATE TABLE IF NOT EXISTS equity_history (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    equity NUMERIC(20, 8) NOT NULL,
    cash_balance NUMERIC(20, 8) NOT NULL,
    positions_value NUMERIC(20, 8) NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('trade', 'deposit', 'withdrawal', 'adjustment', 'reset')),
    related_trade_id BIGINT REFERENCES trades(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE equity_history DROP CONSTRAINT IF EXISTS equity_history_reason_check;
ALTER TABLE equity_history ADD COLUMN IF NOT EXISTS cash_balance NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE equity_history ADD COLUMN IF NOT EXISTS positions_value NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE equity_history ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT 'adjustment';
ALTER TABLE equity_history ADD COLUMN IF NOT EXISTS related_trade_id BIGINT REFERENCES trades(id) ON DELETE SET NULL;
ALTER TABLE equity_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE equity_history SET reason = 'adjustment' WHERE reason IS NULL OR reason NOT IN ('trade', 'deposit', 'withdrawal', 'adjustment', 'reset');
ALTER TABLE equity_history ADD CONSTRAINT equity_history_reason_check CHECK (reason IN ('trade', 'deposit', 'withdrawal', 'adjustment', 'reset'));

CREATE TABLE IF NOT EXISTS daily_snapshots (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    equity NUMERIC(20, 8) NOT NULL,
    cash_balance NUMERIC(20, 8) NOT NULL,
    positions_value NUMERIC(20, 8) NOT NULL,
    total_pnl NUMERIC(20, 8) NOT NULL,
    return_rate NUMERIC(20, 8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, snapshot_date)
);

ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS snapshot_date DATE;
UPDATE daily_snapshots SET snapshot_date = CURRENT_DATE WHERE snapshot_date IS NULL;
ALTER TABLE daily_snapshots ALTER COLUMN snapshot_date SET NOT NULL;
ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS equity NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS cash_balance NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS positions_value NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS total_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS return_rate NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE daily_snapshots ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS signal_history (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD')),
    reason TEXT NOT NULL,
    price NUMERIC(20, 8) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE signal_history DROP CONSTRAINT IF EXISTS signal_history_signal_check;
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'moving_average';
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT '';
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS price NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE signal_history SET signal = 'HOLD' WHERE signal IS NULL OR signal NOT IN ('BUY', 'SELL', 'HOLD');
ALTER TABLE signal_history ADD CONSTRAINT signal_history_signal_check CHECK (signal IN ('BUY', 'SELL', 'HOLD'));

CREATE TABLE IF NOT EXISTS backtest_history (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    return_pct NUMERIC(20, 8) NOT NULL DEFAULT 0,
    max_drawdown NUMERIC(20, 8) NOT NULL DEFAULT 0,
    sharpe_ratio NUMERIC(20, 8) NOT NULL DEFAULT 0,
    win_rate NUMERIC(20, 8) NOT NULL DEFAULT 0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'moving_average';
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT 'AAPL';
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS start_date TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS end_date TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS return_pct NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS max_drawdown NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS sharpe_ratio NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS win_rate NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS trade_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE backtest_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS backtest_results (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    return_pct NUMERIC(20, 8) NOT NULL DEFAULT 0,
    annual_return_pct NUMERIC(20, 8) NOT NULL DEFAULT 0,
    max_drawdown NUMERIC(20, 8) NOT NULL DEFAULT 0,
    win_rate NUMERIC(20, 8) NOT NULL DEFAULT 0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    avg_profit NUMERIC(20, 8) NOT NULL DEFAULT 0,
    avg_loss NUMERIC(20, 8) NOT NULL DEFAULT 0,
    runtime_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT 'AAPL';
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'moving_average';
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS params JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS start_time TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS end_time TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS return_pct NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS annual_return_pct NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS max_drawdown NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS win_rate NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS trade_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS avg_profit NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS avg_loss NUMERIC(20, 8) NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS runtime_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS sharpe_ratio NUMERIC(20, 8) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_user_executed ON trades(user_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_user_source_executed ON trades(user_id, execution_source, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON account_transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_equity_history_user_created ON equity_history(user_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_daily_snapshots_user_date ON daily_snapshots(user_id, snapshot_date ASC);
CREATE INDEX IF NOT EXISTS idx_signal_history_user_created ON signal_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_history_user_created ON backtest_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_history_user_return ON backtest_history(user_id, return_pct DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_results_user_created ON backtest_results(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_results_user_return ON backtest_results(user_id, return_pct DESC);
CREATE INDEX IF NOT EXISTS idx_auto_trading_logs_user_created ON auto_trading_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auto_trading_logs_user_scan ON auto_trading_logs(user_id, scan_id, created_at DESC);
