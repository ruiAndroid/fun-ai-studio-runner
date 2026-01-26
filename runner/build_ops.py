import logging
import os
import shutil
import subprocess
from typing import Optional

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


