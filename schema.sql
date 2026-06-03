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

CREATE TABLE IF NOT EXISTS positions (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    qty NUMERIC(20, 8) NOT NULL CHECK (qty >= 0),
    avg_price NUMERIC(20, 8) NOT NULL CHECK (avg_price >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);

ALTER TABLE positions ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ NOT NULL DEFAULT now();

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
    enabled_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('deposit', 'withdrawal', 'adjustment')),
    amount NUMERIC(20, 6) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
