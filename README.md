 # dogeee-debug from XJU
 # Remote Sandbox Framework

一个把 `autodl_sandbox_agent` 抽象出来的可复用框架：

- 远程机器运行一个轻量 FastAPI sidecar
- 本地通过 CLI / HTTP 控制远程命令
- 统一提供任务、日志、计费观察、空闲关机能力
- 通过 provider preset 适配不同云主机/算力平台

当前内置 preset：

- `generic`
- `autodl`

## 1. 适用场景

适合做：

- 本地编辑代码，远程只负责运行/验证
- 查看作业状态与 stdout/stderr tail
- 记录租机时间与估算成本
- 在空闲时自动关机

不建议把它当成：

- 全功能实验编排平台
- 远程 IDE 替代品
- 长时间远程修 bug 的主工作面

---

## 2. 项目结构

```text
remote-sandbox-framework/
├─ .env.example
├─ examples/
│  └─ autodl/
├─ scripts/
│  └─ start_server.sh
└─ src/remote_sandbox_framework/
   ├─ app.py
   ├─ cli.py
   ├─ config.py
   ├─ models.py
   ├─ providers.py
   └─ state.py
```

---

## 3. 远程部署

在远程 Linux 主机上：

```bash
git clone https://github.com/dogeee-debug/remote-sandbox-framework.git
cd remote-sandbox-framework
cp .env.example .env
vim .env
bash scripts/start_server.sh
```

如果你和我一样，都是在 AutoDL 买的算力，建议你优先参考：

- `examples/autodl/.env.example`
- `examples/autodl/README.md`

---

## 4. 核心环境变量

| 变量 | 说明 |
| --- | --- |
| `REMOTE_SANDBOX_PROVIDER` | provider preset，默认 `generic` |
| `REMOTE_SANDBOX_TOKEN` | Bearer token，必填 |
| `REMOTE_SANDBOX_WORKSPACE` | 允许执行命令的工作区根目录 |
| `REMOTE_SANDBOX_RUNTIME_ROOT` | runtime/job 日志落盘目录 |
| `REMOTE_SANDBOX_HOURLY_RATE` | 每小时费用，便于成本估算 |
| `REMOTE_SANDBOX_ENABLE_SHUTDOWN` | 是否真的执行关机命令 |
| `REMOTE_SANDBOX_SHUTDOWN_COMMAND` | 关机命令 |
| `REMOTE_SANDBOX_DEFAULT_TIMEOUT` | 作业默认超时（秒），0 表示不超时 |

---

## 5. 启动服务

```bash
bash scripts/start_server.sh
```

后台运行：

```bash
nohup bash scripts/start_server.sh > runtime/server.log 2>&1 &
```

---

## 6. 本地连接

推荐你先打 SSH 隧道：

```bash
ssh -L 8787:127.0.0.1:8787 root@YOUR_REMOTE_HOST
```

本地环境变量：

### PowerShell

```powershell
$env:REMOTE_SANDBOX_URL="http://127.0.0.1:8787"
$env:REMOTE_SANDBOX_TOKEN="YOUR_TOKEN"
```

### Linux / macOS

```bash
export REMOTE_SANDBOX_URL=http://127.0.0.1:8787
export REMOTE_SANDBOX_TOKEN=YOUR_TOKEN
```

---

## 7. CLI 用法

安装后可直接使用：

```bash
rsf health
rsf status
rsf run --cwd /root/workspace --command "python train.py" --watch
rsf logs <job_id> --stream stdout --raw
rsf cancel <job_id>
rsf arm-shutdown 600
rsf profiles
```

也可以：

```bash
python -m remote_sandbox_framework.cli status
```

---

## 8. HTTP API

请求头：

```text
Authorization: Bearer <REMOTE_SANDBOX_TOKEN>
```

常用接口：

- `GET /health`
- `GET /status`
- `POST /jobs/run`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/logs`
- `POST /jobs/{job_id}/cancel`
- `POST /lease/start`
- `POST /lease/stop`
- `POST /shutdown/arm`
- `POST /shutdown/disarm`
- `POST /shutdown/now`

---


## 10. 开发验证

```bash
python -m compileall src
```

如果你想把它进一步做成多 provider 插件体系，可以继续在 `providers.py` 基础上扩展。
