import requests

from runner import settings


def deploy_app(agent_base_url: str, user_id: str, app_id: str, image: str, container_port: int = 3000, base_path: str = "") -> None:
    url = agent_base_url.rstrip("/") + "/agent/apps/deploy"
    headers = {"X-Runtime-Token": settings.RUNTIME_AGENT_TOKEN}
    bp = (base_path or "").strip()
    if not bp:
        bp = f"/apps/{app_id}"
    body = {"userId": user_id, "appId": app_id, "image": image, "containerPort": container_port, "basePath": bp}
    r = requests.post(url, json=body, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    # runtime-agent 默认返回非 Result
    if not isinstance(data, dict):
        raise RuntimeError(f"runtime deploy invalid response: {data}")


