import os


def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


DEPLOY_BASE_URL = env("DEPLOY_BASE_URL", "http://127.0.0.1:7002")
RUNNER_ID = env("RUNNER_ID", "runner-01")
JOB_LEASE_SECONDS = int(env("JOB_LEASE_SECONDS", "30"))
POLL_SECONDS = float(env("POLL_SECONDS", "3"))

RUNTIME_AGENT_TOKEN = env("RUNTIME_AGENT_TOKEN", "CHANGE_ME")


