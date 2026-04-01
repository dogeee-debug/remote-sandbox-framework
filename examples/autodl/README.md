# AutoDL preset

如果你的目标是“本地 Codex / 本地开发机控制远程 AutoDL 机器”，推荐工作流仍然是：

1. 本地改代码
2. `rsync` / `scp` 增量同步
3. 远程 `tmux` 执行训练/预处理
4. 用本框架查看状态、抓日志、观察费用、空闲关机

## 推荐配置

```bash
cp examples/autodl/.env.example .env
vim .env
bash scripts/start_server.sh
```

## 本地调用示例

```powershell
$env:REMOTE_SANDBOX_URL="http://127.0.0.1:8787"
$env:REMOTE_SANDBOX_TOKEN="YOUR_TOKEN"
rsf status
rsf run --cwd /root/autodl-tmp/CodeBase --command "bash scripts/cloud/run.sh" --watch --shutdown-after 600
```
