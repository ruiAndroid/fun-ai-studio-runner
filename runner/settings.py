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
# Build 时是否强制拉取最新基础镜像（避免 Harbor 侧 latest 已更新但本机仍用旧 cache）
# - docker/podman 都支持 --pull
# - 默认 true：更稳定；如果你极度追求速度可设为 false
RUNNER_DOCKER_BUILD_PULL = env("RUNNER_DOCKER_BUILD_PULL", "true").lower() != "false"
ACR_REGISTRY = env("ACR_REGISTRY", "")  # e.g. crpi-xxx.cn-hangzhou.personal.cr.aliyuncs.com
ACR_NAMESPACE = env("ACR_NAMESPACE", "funaistudio")  # e.g. funaistudio

# Registry Auth（可选）：配置后 Runner 会在 build/push 前自动 login（兼容 docker/podman）
# 说明：为兼容历史配置，REGISTRY_* 未配置时会 fallback 到 ACR_*。
# 兼容旧变量（仍保留；后续可逐步替换为 REGISTRY_*）
ACR_USERNAME = env("ACR_USERNAME", "")
ACR_PASSWORD = env("ACR_PASSWORD", "")

REGISTRY_USERNAME = env("REGISTRY_USERNAME", "") or ACR_USERNAME
REGISTRY_PASSWORD = env("REGISTRY_PASSWORD", "") or ACR_PASSWORD


