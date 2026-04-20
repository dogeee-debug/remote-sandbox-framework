# AutoDL Preset

AutoDL support is still part of the project, but it is now a secondary preset rather than the main homepage story.

If your goal is "edit locally, run on a remote AutoDL machine", the recommended workflow is still:

1. edit code locally
2. sync changes with `rsync` / `scp`
3. run training or preprocessing in remote `tmux`
4. use this framework for status, logs, lease cost visibility, and idle shutdown

## Recommended Setup

```bash
cp examples/autodl/.env.example .env
vim .env
bash scripts/start_server.sh
```

## Local Usage

PowerShell:

```powershell
$env:REMOTE_SANDBOX_URL="http://127.0.0.1:8787"
$env:REMOTE_SANDBOX_TOKEN="YOUR_TOKEN"
rsf status
rsf run --cwd /root/autodl-tmp/CodeBase --command "bash scripts/cloud/run.sh" --watch --shutdown-after 600
```

## Where AutoDL Fits Now

Use the AutoDL preset when you want:

- a small remote sidecar instead of a full remote IDE
- controlled job execution
- stdout/stderr tail access
- lease duration and cost estimates
- automatic shutdown after success or idle time

If you are evaluating the new artifact-first autoresearch direction, start with:

- [README.md](../../README.md)
- [examples/autoresearch/repo-research/README.md](../autoresearch/repo-research/README.md)
