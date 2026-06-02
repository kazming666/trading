# Render 部署说明

这个版本是服务端架构：用户、账户余额、持仓、订单、成交、自选股、资产历史和每日资产快照都保存在 PostgreSQL。

## 数据库表

`server.py` 启动时会自动执行 `schema.sql`，不需要手动执行 SQL。已有数据库会通过 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 自动补齐新字段。

核心表：

- `users`：注册用户、密码盐和密码哈希。
- `sessions`：登录会话和 Cookie token 哈希。
- `accounts`：现金余额、初始资金、基准币种、当前股票。
- `watchlist`：用户自选股。
- `positions`：持仓、均价、币种、首次买入时间。
- `orders`：模拟订单，当前按真实最新价即时成交。
- `trades`：成交记录，包含成交后余额、成交后持仓、成交前后总资产。
- `account_transactions`：资金流水，当前用于入金记录。
- `equity_history`：每次成交、入金、重置后的账户总资产记录。
- `daily_snapshots`：每日资产快照，包含总资产、现金、持仓市值、总盈亏和收益率。

## Render 配置

1. 在 Render 创建 PostgreSQL 数据库。
2. 在 Web Service 绑定数据库，确认环境变量包含：

```text
DATABASE_URL=postgresql://...
```

3. Web Service 设置：

```text
Build Command: pip install -r requirements.txt
Start Command: HOST=0.0.0.0 python server.py
Health Check Path: /api/health
```

如果使用仓库里的 `render.yaml`，这些配置会自动带上。

4. 部署后检查：

```text
/api/health
/api/me
/api/state
/api/history?symbol=AAPL&range=1d
```

`/api/health` 返回 `database: true` 表示 PostgreSQL 已连接。

## 本地运行

本地运行也需要 PostgreSQL。设置 `DATABASE_URL` 后执行：

```powershell
pip install -r requirements.txt
$env:DATABASE_URL="postgresql://user:password@host:5432/dbname"
python server.py
```

打开：

```text
http://127.0.0.1:8765/
```

## 本次更新

- 修复账户资产曲线的真实资产读取、单点数据缩放和美元金额格式化。
- 新增资产曲线范围：今日、本周、本月、全部。
- 新增 `daily_snapshots` 每日资产快照表并自动写入。
- 持仓展示当前价格、浮动盈亏、浮动收益率、持仓天数和买入时间。
- 交易记录展示成交后余额、成交后持仓、成交前后总资产和变化。
- 新增“导出交易记录”CSV。

## 页面结构

前端现在是单页应用风格，不刷新整站即可切换页面：

- `Dashboard`：账户总资产、现金、持仓市值、总盈亏、资产曲线。
- `Trading`：股票搜索、实时行情图、买卖下单。
- `Portfolio`：当前持仓和盈亏分析指标。
- `History`：交易记录、资金入金记录、CSV 导出。
- `Watchlist`：自选股列表。
- `Settings`：修改密码、入金、初始资金设置、重置模拟账户。

`requirements.txt` 使用 `psycopg[binary]`，不锁定具体版本。修改密码接口为 `/api/auth/password`，会更新现有 `users` 表中的密码盐和哈希，不需要新增表。
