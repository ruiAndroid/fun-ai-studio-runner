import os
from typing import Optional


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


DEPLOY_BASE_URL = env("DEPLOY_BASE_URL", "http://127.0.0.1:7002")
RUNNER_ID = env("RUNNER_ID", "runner-01")
JOB_LEASE_SECONDS = int(env("JOB_LEASE_SECONDS", "30"))
POLL_SECONDS = float(env("POLL_SECONDS", "3"))

RUNTIME_AGENT_TOKEN = env("RUNTIME_AGENT_TOKEN", "CHANGE_ME")

# Git 拉取（Runner bot）
GIT_SSH_KEY_PATH = env("GIT_SSH_KEY_PATH", "/opt/fun-ai-studio/keys/gitea/runner_bot_ed25519")
GIT_KNOWN_HOSTS_PATH = env("GIT_KNOWN_HOSTS_PATH", "/opt/fun-ai-studio/keys/gitea/known_hosts")

# Build & Push
RUNNER_WORKDIR = env("RUNNER_WORKDIR", "/data/funai/runner/workdir")
RUNNER_DOCKER_BIN = env("RUNNER_DOCKER_BIN", "docker")
ACR_REGISTRY = env("ACR_REGISTRY", "")  # e.g. crpi-xxx.cn-hangzhou.personal.cr.aliyuncs.com
ACR_NAMESPACE = env("ACR_NAMESPACE", "funaistudio")  # e.g. funaistudio


