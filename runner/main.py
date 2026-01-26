import os
import time
import threading
import logging

from runner.logging_setup import setup_logging
from runner.deploy_client import claim_job, report_job, heartbeat_job
from runner.build_ops import ensure_clean_dir, git_clone, docker_build, docker_push, docker_rmi, acr_delete_image
from runner.runtime_client import deploy_app
from runner import settings


def main() -> None:
    setup_logging("fun-ai-studio-runner")
    log = logging.getLogger("runner.main")
    log.info(
        "runner started: runnerId=%s, deployBaseUrl=%s, pollSeconds=%s, leaseSeconds=%s",
        settings.RUNNER_ID,
        settings.DEPLOY_BASE_URL,
        settings.POLL_SECONDS,
        settings.JOB_LEASE_SECONDS,
    )
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
            log.info(
                "claimed job: jobId=%s, appId=%s, basePath=%s, port=%s, agentBaseUrl=%s",
                job_id,
                app_id,
                base_path,
                port,
                agent_base_url,
            )

            # 在执行期间定期 heartbeat 续租（避免 build/push 超过 leaseSeconds 导致任务卡死）
            stop_hb = threading.Event()
            hb_lock = threading.Lock()

            def _hb_loop():
                # extendSeconds 用 JOB_LEASE_SECONDS；间隔取一半（最小 5s）
                interval = max(5, int(settings.JOB_LEASE_SECONDS / 2))
                while not stop_hb.is_set():
                    try:
                        with hb_lock:
                            data = heartbeat_job(str(job_id), int(settings.JOB_LEASE_SECONDS))
                        status = str((data or {}).get("status") or "")
                        # 控制面已判定非 RUNNING：停止续租线程（主流程可在下一步感知到并退出）
                        if status and status != "RUNNING":
                            stop_hb.set()
                            return
                    except Exception as e:
                        # heartbeat 失败不应中断主流程（但可能导致 lease 过期被回收）
                        log.warning("heartbeat failed: jobId=%s err=%s", job_id, str(e))
                    stop_hb.wait(interval)

            hb_t = threading.Thread(target=_hb_loop, name=f"hb-{job_id}", daemon=True)
            hb_t.start()

            # 1) 确定 image：优先使用 payload.image；否则从 Git build 并 push
            image = str(payload.get("image") or "")
            built_image = False  # 标记是否为本次构建的镜像（用于后续清理）
            if not image:
                repo_ssh_url = str(payload.get("repoSshUrl") or "")
                git_ref = str(payload.get("gitRef") or "main")

                acr_registry = str(payload.get("acrRegistry") or settings.ACR_REGISTRY or "")
                acr_namespace = str(payload.get("acrNamespace") or settings.ACR_NAMESPACE or "")
                if not acr_registry:
                    raise RuntimeError("missing ACR_REGISTRY (required when payload.image not provided)")

                work_root = settings.RUNNER_WORKDIR or "/tmp/funai-runner-workdir"
                work_dir = os.path.join(work_root, f"app-{app_id}")
                log.info("git clone: jobId=%s repo=%s ref=%s -> %s", job_id, repo_ssh_url, git_ref, work_dir)
                with hb_lock:
                    heartbeat_job(str(job_id), int(settings.JOB_LEASE_SECONDS), phase="CLONE", phase_message=f"clone {git_ref}")
                ensure_clean_dir(work_dir)
                git_clone(repo_ssh_url, git_ref, work_dir)

                # image tag 策略：默认 latest；允许前端/控制面传入 imageTag 作为覆盖
                image_tag = str(payload.get("imageTag") or "latest")
                image = f"{acr_registry}/{acr_namespace}/u{payload.get('userId')}-app{app_id}:{image_tag}"
                log.info("docker build: jobId=%s image=%s", job_id, image)
                with hb_lock:
                    heartbeat_job(str(job_id), int(settings.JOB_LEASE_SECONDS), phase="BUILD", phase_message="docker build")
                docker_build(image, work_dir, registry=acr_registry)
                log.info("docker push: jobId=%s image=%s", job_id, image)
                with hb_lock:
                    heartbeat_job(str(job_id), int(settings.JOB_LEASE_SECONDS), phase="PUSH", phase_message="docker push")
                docker_push(image, registry=acr_registry)
                built_image = True  # 标记为本次构建
            else:
                log.info("use existing image from payload: jobId=%s image=%s", job_id, image)

            if not job_id or not app_id or not image or not agent_base_url:
                raise RuntimeError(f"missing fields: jobId={job_id}, appId={app_id}, image={image}, agentBaseUrl={agent_base_url}")

            user_id = str(payload.get("userId") or "")
            log.info("runtime deploy: jobId=%s userId=%s appId=%s image=%s port=%s", job_id, user_id, app_id, image, port)
            with hb_lock:
                heartbeat_job(str(job_id), int(settings.JOB_LEASE_SECONDS), phase="DEPLOY", phase_message="runtime deploy")
            deploy_app(agent_base_url, user_id, app_id, image, port, base_path=base_path)
            log.info("report SUCCEEDED: jobId=%s", job_id)
            report_job(job_id, "SUCCEEDED")
            stop_hb.set()

            # 清理镜像（仅清理本次构建的镜像，节省存储）
            if built_image and image:
                log.info("cleanup image: jobId=%s image=%s", job_id, image)
                try:
                    docker_rmi(image)  # 删除本地镜像
                except Exception as e:
                    log.warning("failed to remove local image: %s", e)
                try:
                    acr_delete_image(image)  # 删除 ACR 远程镜像
                except Exception as e:
                    log.warning("failed to delete acr image: %s", e)
        except Exception as e:
            log.exception("job failed: err=%s", str(e))
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


