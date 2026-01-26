import logging
import os
import shutil
import subprocess
from typing import Optional

import requests

from runner import settings

log = logging.getLogger("runner.build_ops")


def _run(cmd: list, cwd: Optional[str] = None, timeout: int = 900) -> str:
    p = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stdout}")
    return p.stdout


def docker_login(registry: str) -> None:
    """
    Best-effort login to registry.

    - If ACR_USERNAME/ACR_PASSWORD are provided, do non-interactive login via --password-stdin.
    - Otherwise, assume machine has been logged in once (docker/podman stores creds on disk) and do nothing.
    """
    if not registry:
        return
    user = (settings.ACR_USERNAME or "").strip()
    pwd = (settings.ACR_PASSWORD or "").strip()
    if not user or not pwd:
        return

    bin_ = settings.RUNNER_DOCKER_BIN or "docker"
    # Use password-stdin to avoid leaking password in process args/logs.
    p = subprocess.run(
        [bin_, "login", registry, "-u", user, "--password-stdin"],
        input=pwd + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=False,
    )
    if p.returncode != 0:
        # Avoid printing password; include stdout for troubleshooting.
        raise RuntimeError(
            "ACR login failed. Please verify ACR_USERNAME/ACR_PASSWORD or login manually once on this machine.\n"
            f"command failed ({p.returncode}): {bin_} login {registry} -u {user} --password-stdin\n"
            f"{p.stdout}"
        )


def build_git_ssh_command() -> str:
    key = settings.GIT_SSH_KEY_PATH or ""
    kh = settings.GIT_KNOWN_HOSTS_PATH or ""
    if not key or not kh:
        raise RuntimeError("GIT_SSH_KEY_PATH / GIT_KNOWN_HOSTS_PATH not configured")
    return f"ssh -i {key} -o UserKnownHostsFile={kh} -o StrictHostKeyChecking=yes"


def ensure_clean_dir(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


def git_clone(repo_ssh_url: str, git_ref: str, dest_dir: str) -> None:
    if not repo_ssh_url:
        raise RuntimeError("repoSshUrl is empty")
    ref = git_ref or "main"
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = build_git_ssh_command()
    # shallow clone by branch/tag; if it's a commit sha, fallback to normal clone + checkout
    try:
        p0 = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, repo_ssh_url, dest_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=600,
            env=env,
        )
        if p0.returncode != 0:
            raise RuntimeError(p0.stdout)
        return
    except Exception:
        ensure_clean_dir(dest_dir)
        p = subprocess.run(
            ["git", "clone", repo_ssh_url, dest_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=900,
            env=env,
        )
        if p.returncode != 0:
            raise RuntimeError(f"git clone failed: {p.stdout}")
        p2 = subprocess.run(
            ["git", "checkout", ref],
            cwd=dest_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
            env=env,
        )
        if p2.returncode != 0:
            raise RuntimeError(f"git checkout {ref} failed: {p2.stdout}")


def docker_build(image: str, context_dir: str, registry: Optional[str] = None) -> None:
    if not image:
        raise RuntimeError("image tag is empty")
    if registry:
        docker_login(registry)
    bin_ = settings.RUNNER_DOCKER_BIN or "docker"
    _run([bin_, "build", "-t", image, context_dir], cwd=context_dir, timeout=1800)


def docker_push(image: str, registry: Optional[str] = None) -> None:
    if not image:
        raise RuntimeError("image tag is empty")
    if registry:
        docker_login(registry)
    bin_ = settings.RUNNER_DOCKER_BIN or "docker"
    _run([bin_, "push", image], timeout=900)


def docker_rmi(image: str) -> None:
    """删除本地镜像（best-effort，不抛异常）"""
    if not image:
        return
    bin_ = settings.RUNNER_DOCKER_BIN or "docker"
    try:
        subprocess.run(
            [bin_, "rmi", "-f", image],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            check=False,
        )
        log.info("local image removed: %s", image)
    except Exception as e:
        log.warning("failed to remove local image %s: %s", image, e)


def acr_delete_image(image: str) -> None:
    """
    删除 ACR 远程镜像（best-effort，不抛异常）。
    
    使用 Docker Registry V2 API：
    1. 获取 manifest digest
    2. DELETE /v2/{name}/manifests/{digest}
    
    需要配置 ACR_USERNAME / ACR_PASSWORD。
    """
    if not image:
        return
    user = (settings.ACR_USERNAME or "").strip()
    pwd = (settings.ACR_PASSWORD or "").strip()
    if not user or not pwd:
        log.debug("skip acr delete: ACR_USERNAME/ACR_PASSWORD not configured")
        return

    # 解析 image: registry/namespace/repo:tag
    # 例如: crpi-xxx.cn-hangzhou.personal.cr.aliyuncs.com/funaistudio/u1-app123:latest
    try:
        if "/" not in image:
            log.warning("invalid image format for acr delete: %s", image)
            return
        
        parts = image.split("/", 1)
        registry = parts[0]
        rest = parts[1]  # namespace/repo:tag
        
        if ":" in rest:
            name, tag = rest.rsplit(":", 1)
        else:
            name, tag = rest, "latest"
        
        # 1. 获取 manifest digest
        url = f"https://{registry}/v2/{name}/manifests/{tag}"
        headers = {
            "Accept": "application/vnd.docker.distribution.manifest.v2+json"
        }
        resp = requests.get(url, auth=(user, pwd), headers=headers, timeout=30)
        if resp.status_code == 404:
            log.info("acr image not found (already deleted?): %s", image)
            return
        if resp.status_code != 200:
            log.warning("failed to get manifest for %s: %s %s", image, resp.status_code, resp.text[:200])
            return
        
        digest = resp.headers.get("Docker-Content-Digest")
        if not digest:
            log.warning("no digest in manifest response for %s", image)
            return
        
        # 2. 删除 manifest
        delete_url = f"https://{registry}/v2/{name}/manifests/{digest}"
        del_resp = requests.delete(delete_url, auth=(user, pwd), timeout=30)
        if del_resp.status_code in (200, 202, 204):
            log.info("acr image deleted: %s (digest=%s)", image, digest[:20])
        else:
            log.warning("failed to delete acr image %s: %s %s", image, del_resp.status_code, del_resp.text[:200])
    except Exception as e:
        log.warning("acr delete failed for %s: %s", image, e)


