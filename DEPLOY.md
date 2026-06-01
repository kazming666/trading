# 部署成公网地址

这个应用不能直接把 `127.0.0.1` 当永久地址，因为它只代表当前电脑。要有公网地址，需要部署到云平台。

## 推荐方式：Render

1. 把当前文件夹上传到一个 GitHub 仓库。
2. 打开 Render，创建一个新的 Web Service。
3. 选择这个 GitHub 仓库。
4. 启动命令填写：

```bash
HOST=0.0.0.0 python server.py
```

5. 部署完成后，Render 会给你一个类似下面这样的公网地址：

```text
https://paper-trading-desk.onrender.com
```

以后就用这个地址访问，不需要打开 `127.0.0.1:8765`。

## 其他可选方式

- VPS：买一台云服务器，用 `python server.py` 配合 Nginx 和域名。
- Railway/Fly.io：和 Render 类似，上传代码后运行 `HOST=0.0.0.0 python server.py`。
- Cloudflare Tunnel：适合把你本机临时暴露到公网，但电脑关机后地址也会失效，不算真正永久。

## 注意

免费托管平台可能会休眠，第一次打开会慢一点。要完全稳定，需要付费实例或自己的服务器。
