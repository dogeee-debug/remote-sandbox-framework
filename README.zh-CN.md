# Remote Sandbox Framework

一个面向 GitHub 开发者的极简 autoresearch 多智能体协作内核。

这个项目现在主打的是：

- agent 通过产物协作，而不是靠无限对话漂移
- 执行层是确定性的
- 远程 shell 权限边界清晰
- 每次运行都有事件账本和状态快照
- 模型默认负责“提案和整理”，不是直接拿远程机器全权限

## 当前定位

仓库已经从“AutoDL 远程助手抽象”升级为一个更通用的最小内核：

- `board` 负责 artifact-first 的多智能体工作流
- `manifest scheduler` 负责分阶段、可恢复的确定性执行
- `FastAPI sidecar + CLI` 负责本地到远程主机的安全控制

一句话理解：

> 让 agent 协作更可复现、更可追踪、更容易接到真实远程环境里。

## Phase 1 新增能力

### 1. Board 工作流

新增 manifest v2：

- `kind: "board"`
- `version: 2`
- `artifacts[]`
- `tasks[]`

固定任务模式：

- `llm_propose`
- `shell_stage`
- `synthesis`
- `review_gate`

### 2. 可追踪运行产物

每次 `board-run` 会生成：

- `events.ndjson`
- `state.json`
- `summary.json`
- `artifacts/`

这让运行过程可以被回放、调试和审查。

### 3. Repo Research Demo

示例目录：

- [examples/autoresearch/repo-research/README.md](examples/autoresearch/repo-research/README.md)

流程：

1. 扫描仓库
2. 生成 brief
3. 提取 evidence
4. 综合成报告
5. review gate 校验

即使不接 LLM，也能本地确定性跑通。

## 常用命令

```bash
rsf board-run --manifest examples/autoresearch/repo-research/board.json
rsf board-status --run-dir runtime/board-runs/<run_id>
rsf board-replay --run-dir runtime/board-runs/<run_id>
rsf board-init --preset repo-research --dest ./my-board
```

保留原有远程执行能力：

```bash
rsf status
rsf run --cwd /root/workspace --command "python train.py" --watch
rsf manifest-reconcile --manifest examples/manifest.local.json
rsf manifest-schedule --manifest examples/manifest.local.json --queue-log runtime/scheduler.log --runs-dir runtime/runs
```

## AutoDL 仍然支持

AutoDL 现在是次级入口，不再是仓库首页主叙事。

对应文档见：

- [examples/autodl/README.md](examples/autodl/README.md)

## 建议阅读顺序

1. 先看英文主页 [README.md](README.md)
2. 再跑 `repo-research` demo
3. 最后根据需要接入远程 runner / AutoDL preset
