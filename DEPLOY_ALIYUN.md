# 阿里云 ECS 部署

本项目推荐用 Docker Compose 部署。业务服务不需要对公网开放 HTTP 端口，安全组只需要开放 SSH 端口即可；出站需要能访问 AkShare 数据源、DeepSeek API，以及企业微信机器人 Webhook。健康检查端口默认只绑定宿主机回环地址 `127.0.0.1:18080`，用于本机巡检，不对公网暴露。

## 1. 服务器准备

以 Ubuntu 22.04/24.04 为例：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

可选：让当前用户免 `sudo` 运行 Docker，执行后需要重新登录：

```bash
sudo usermod -aG docker "$USER"
```

## 2. 上传项目

在本机项目目录执行，替换服务器信息：

```bash
./scripts/package_source.sh
scp futures-signal-src.tar.gz root@YOUR_ECS_PUBLIC_IP:/opt/
ssh root@YOUR_ECS_PUBLIC_IP
mkdir -p /opt/futures-signal
tar -xzf /opt/futures-signal-src.tar.gz -C /opt/futures-signal
```

也可以直接用 `rsync`：

```bash
rsync -av --delete \
  --exclude .git \
  --exclude .venv \
  --exclude .env \
  --exclude data \
  --exclude logs \
  ./ root@YOUR_ECS_PUBLIC_IP:/opt/futures-signal/
```

不要上传本机 `.env`、`data/market.db` 和日志文件；VPS 上重新复制 `.env.example` 后填写密钥。

## 3. 配置环境变量

登录服务器：

```bash
ssh root@YOUR_ECS_PUBLIC_IP
cd /opt/futures-signal
cp .env.example .env
nano .env
```

至少填写：

```env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
DEEPSEEK_API_KEY=...
TZ=Asia/Shanghai
```

如果服务器出站网络需要代理，可在 `.env` 里配置：

```env
HTTPS_PROXY=http://proxy-host:proxy-port
HTTP_PROXY=http://proxy-host:proxy-port
```

## 4. 启动服务

```bash
docker compose up -d --build
docker compose logs -f
curl http://127.0.0.1:18080/healthz
```

验证企业微信推送：

```bash
docker compose run --rm futures-signal python -m futures_signal test-wecom
```

验证 DeepSeek：

```bash
docker compose run --rm futures-signal python -m futures_signal test-ai
```

查看交易日历状态：

```bash
docker compose run --rm futures-signal python -m futures_signal calendar
```

查看容器健康检查响应：

```bash
curl http://127.0.0.1:18080/healthz
```

返回 `200 OK`，包含基础字段 `status`、`service`、`time`、`uptime_seconds`、`worker`、`storage`。该接口只做只读状态检查，不返回机器人密钥、AI 密钥或策略明细。

## 5. 运维命令

查看状态：

```bash
docker compose ps
```

期望看到 `healthy` 状态；Compose 已内置基于 `http://127.0.0.1:18080/healthz` 的容器健康探测。

查看日志：

```bash
docker compose logs -f --tail=200
```

更新代码后重启：

```bash
docker compose up -d --build
```

停止服务：

```bash
docker compose down
```

数据和日志持久化在服务器：

- `/opt/futures-signal/data/market.db`
- `/opt/futures-signal/logs/futures_signal.log`
