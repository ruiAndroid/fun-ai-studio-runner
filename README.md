# fun-ai-studio-runner

Runner（执行面）：从 Deploy 控制面领取 Job，执行构建/发布动作，并回传结果。

## 当前定位（最小闭环）

- 轮询 Deploy：`POST /deploy/jobs/claim`
- 执行（示例）：构建镜像并 push（后续接入 buildx）
- 调用 Runtime-Agent：`POST /agent/apps/deploy`
- 回传 Deploy：`POST /deploy/jobs/{jobId}/report`

## 配置（环境变量）

- `DEPLOY_BASE_URL=http://<deploy-host>:7002`
- `RUNNER_ID=runner-01`
- `JOB_LEASE_SECONDS=30`
- `POLL_SECONDS=3`
- `RUNTIME_AGENT_TOKEN=CHANGE_ME`

## 启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python runner/main.py
```


