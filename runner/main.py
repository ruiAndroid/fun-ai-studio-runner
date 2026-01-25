import os
import time
import threading

from runner.logging_setup import setup_logging
from runner.deploy_client import claim_job, report_job, heartbeat_job
from runner.build_ops import ensure_clean_dir, git_clone, docker_build, docker_push
from runner.runtime_client import deploy_app
from runner import settings


def main() -> None:
    setup_logging("fun-ai-studio-runner")
    while True:
        try:
            job = claim_job()
            if not job:
                time.sleep(settings.POLL_SECONDS)
                continue

            job_id = job.get("id")
            runtime_node = job.get("runtimeNode") or {}
            payload = job.get("payload") or {}

            # 约定：payload 至少包含 appId；agentBaseUrl 由 Deploy 的 runtimeNode 下发（A 方案）
            app_id = str(payload.get("appId") or "")
            agent_base_url = str(runtime_node.get("agentBaseUrl") or "")
            port = int(payload.get("containerPort") or 3000)
            base_path = str(payload.get("basePath") or "").strip()

            # 在执行期间定期 heartbeat 续租（避免 build/push 超过 leaseSeconds 导致任务卡死）
            stop_hb = threading.Event()

            def _hb_loop():
                # extendSeconds 用 JOB_LEASE_SECONDS；间隔取一半（最小 5s）
                interval = max(5, int(settings.JOB_LEASE_SECONDS / 2))
                while not stop_hb.is_set():
                    try:
                        heartbeat_job(str(job_id), int(settings.JOB_LEASE_SECONDS))
                    except Exception:
                        # heartbeat 失败不应中断主流程（但可能导致 lease 过期被回收）
                        pass
                    stop_hb.wait(interval)

            hb_t = threading.Thread(target=_hb_loop, name=f"hb-{job_id}", daemon=True)
            hb_t.start()

            # 1) 确定 image：优先使用 payload.image；否则从 Git build 并 push
            image = str(payload.get("image") or "")
            if not image:
                repo_ssh_url = str(payload.get("repoSshUrl") or "")
                git_ref = str(payload.get("gitRef") or "main")

                acr_registry = str(payload.get("acrRegistry") or settings.ACR_REGISTRY or "")
                acr_namespace = str(payload.get("acrNamespace") or settings.ACR_NAMESPACE or "")
                if not acr_registry:
                    raise RuntimeError("missing ACR_REGISTRY (required when payload.image not provided)")

                work_root = settings.RUNNER_WORKDIR or "/tmp/funai-runner-workdir"
                work_dir = os.path.join(work_root, f"app-{app_id}")
                ensure_clean_dir(work_dir)
                git_clone(repo_ssh_url, git_ref, work_dir)

                # image tag 策略：默认 latest；允许前端/控制面传入 imageTag 作为覆盖
                image_tag = str(payload.get("imageTag") or "latest")
                image = f"{acr_registry}/{acr_namespace}/u{payload.get('userId')}-app{app_id}:{image_tag}"
                docker_build(image, work_dir)
                docker_push(image)

            if not job_id or not app_id or not image or not agent_base_url:
                raise RuntimeError(f"missing fields: jobId={job_id}, appId={app_id}, image={image}, agentBaseUrl={agent_base_url}")

            deploy_app(agent_base_url, app_id, image, port, base_path=base_path)
            report_job(job_id, "SUCCEEDED")
            stop_hb.set()
        except Exception as e:
            # best effort: if we know job_id try report failed (not always available)
            try:
                if "job_id" in locals() and locals().get("job_id"):
                    report_job(locals()["job_id"], "FAILED", str(e))
            except Exception:
                pass
            try:
                if "stop_hb" in locals() and locals().get("stop_hb"):
                    locals()["stop_hb"].set()
            except Exception:
                pass
            time.sleep(settings.POLL_SECONDS)


if __name__ == "__main__":
    main()


