import os
import shutil
import subprocess
from typing import Optional

from runner import settings


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


def docker_build(image: str, context_dir: str) -> None:
    if not image:
        raise RuntimeError("image tag is empty")
    bin_ = settings.RUNNER_DOCKER_BIN or "docker"
    _run([bin_, "build", "-t", image, context_dir], cwd=context_dir, timeout=1800)


def docker_push(image: str) -> None:
    if not image:
        raise RuntimeError("image tag is empty")
    bin_ = settings.RUNNER_DOCKER_BIN or "docker"
    _run([bin_, "push", image], timeout=900)


