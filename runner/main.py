import time

from runner.logging_setup import setup_logging
from runner.deploy_client import claim_job, report_job
from runner.runtime_client import deploy_app
from runner.settings import POLL_SECONDS


def main() -> None:
    setup_logging("fun-ai-studio-runner")
    while True:
        try:
            job = claim_job()
            if not job:
                time.sleep(POLL_SECONDS)
                continue

            job_id = job.get("id")
            runtime_node = job.get("runtimeNode") or {}
            payload = job.get("payload") or {}

            # 约定：payload 至少包含 appId；image/containerPort 可来自 payload；agentBaseUrl 由 Deploy 的 runtimeNode 下发（A 方案）
            app_id = str(payload.get("appId") or "")
            image = str(payload.get("image") or "")
            agent_base_url = str(runtime_node.get("agentBaseUrl") or "")
            port = int(payload.get("containerPort") or 3000)

            if not job_id or not app_id or not image or not agent_base_url:
                raise RuntimeError(f"missing fields: jobId={job_id}, appId={app_id}, image={image}, agentBaseUrl={agent_base_url}")

            deploy_app(agent_base_url, app_id, image, port)
            report_job(job_id, "SUCCEEDED")
        except Exception as e:
            # best effort: if we know job_id try report failed (not always available)
            try:
                if "job_id" in locals() and locals().get("job_id"):
                    report_job(locals()["job_id"], "FAILED", str(e))
            except Exception:
                pass
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()


