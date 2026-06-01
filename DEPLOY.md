# Render 部署说明

这个版本是服务端架构：用户、账户余额、持仓、订单、成交和入金记录都存 PostgreSQL。

## 数据库结构

核心表：

- `users`：用户账号、密码哈希、展示名。
- `sessions`：登录会话，保存 cookie token 的哈希和过期时间。
- `accounts`：每个用户一个模拟账户，保存现金、初始资金、基准币种、当前股票。
- `watchlist`：用户自选股票。
- `positions`：用户持仓，按 `user_id + symbol` 唯一。
- `orders`：模拟订单，当前下单会立即按真实最新价成交，状态为 `filled`。
- `trades`：成交记录。
- `account_transactions`：账户资金流水，目前用于入金记录。

完整建表 SQL 在 `schema.sql`。`server.py` 启动时会自动执行它，所以 Render 部署后一般不需要手动跑 SQL。

## Render 步骤

1. 在 Render 创建 PostgreSQL 数据库。
2. 在你的 Web Service 里绑定数据库，确保环境变量里有：

```text
DATABASE_URL=postgresql://...
```

3. Web Service 设置：

```text
Build Command: pip install -r requirements.txt
Start Command: HOST=0.0.0.0 python server.py
Health Check Path: /api/health
```

如果使用仓库里的 `render.yaml`，这些会自动配置大半。

4. 重新部署：

```text
Manual Deploy -> Clear build cache & deploy
```

## 本地运行

本地也需要 PostgreSQL。设置 `DATABASE_URL` 后运行：

```powershell
pip install -r requirements.txt
$env:DATABASE_URL="postgresql://user:password@host:5432/dbname"
python server.py
```

打开：

```text
http://127.0.0.1:8765/
```

## 验证接口

```text
/api/health
/api/me
/api/state
/api/history?symbol=AAPL&range=1d
```

`/api/health` 返回 `database: true` 才表示数据库已连接。

Last updated: equity history and statistics deployment trigger.
