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

        accept = "application/vnd.docker.distribution.manifest.v2+json"
        accept_fallbacks = [
            "application/vnd.docker.distribution.manifest.v2+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.v1+json",
            "*/*",
        ]

        def _parse_bearer_challenge(www_auth: str) -> dict:
            # Example:
            # Bearer realm="https://xxx/token",service="registry",scope="repository:foo/bar:pull"
            if not www_auth:
                return {}
            s = www_auth.strip()
            if not s.lower().startswith("bearer "):
                return {}
            kv = s[len("Bearer ") :].strip()
            out = {}
            for part in kv.split(","):
                part = part.strip()
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"')
                out[k] = v
            return out

        def _get_bearer_token_from_challenge(ch: dict, scope: str) -> str:
            """
            Use WWW-Authenticate challenge to fetch bearer token.
            Some registries (incl. some ACR setups) only return challenge on manifest endpoints,
            not on /v2/ ping, so we must rely on the challenge itself.
            """
            realm = (ch or {}).get("realm") or ""
            service = (ch or {}).get("service") or ""
            if not realm:
                # fallback: try ping /v2/ to discover realm/service
                try:
                    ping = requests.get(f"https://{registry}/v2/", timeout=10)
                    ch2 = _parse_bearer_challenge(ping.headers.get("WWW-Authenticate", ""))
                    realm = realm or (ch2.get("realm") or "")
                    service = service or (ch2.get("service") or "")
                except Exception:
                    pass
            if not realm:
                return ""

            params = {}
            if service:
                params["service"] = service
            if scope:
                params["scope"] = scope
            r = requests.get(realm, params=params, auth=(user, pwd), timeout=15)
            if r.status_code != 200:
                log.warning("acr token request failed: http=%s body=%s", r.status_code, (r.text or "")[:200])
                return ""
            try:
                data = r.json()
            except Exception:
                return ""
            return str(data.get("token") or data.get("access_token") or "")

        def _request_with_token(method: str, url: str, scope: str, **kwargs):
            # Try without token first; if 401 with Bearer challenge, fetch token and retry.
            r0 = requests.request(method, url, timeout=30, **kwargs)
            if r0.status_code != 401:
                return r0
            ch = _parse_bearer_challenge(r0.headers.get("WWW-Authenticate", ""))
            # IMPORTANT:
            # - For DELETE, some registries return a challenge scope that doesn't include "delete".
            # - We must request the desired scope explicitly (caller provides it).
            need_scope = scope or ch.get("scope") or ""
            token = _get_bearer_token_from_challenge(ch, need_scope)
            if not token:
                return r0
            headers = dict((kwargs.get("headers") or {}))
            headers["Authorization"] = f"Bearer {token}"
            return requests.request(method, url, timeout=30, headers=headers, **{k: v for k, v in kwargs.items() if k != "headers"})

        def _repo_tags_exist(tag_to_check: str) -> bool:
            tags_url = f"https://{registry}/v2/{name}/tags/list"
            pull_scope2 = f"repository:{name}:pull"
            r = _request_with_token("GET", tags_url, scope=pull_scope2)
            if r.status_code == 401:
                log.warning("acr unauthorized to list tags (check ACR_USERNAME/ACR_PASSWORD): %s/%s", name, tag_to_check)
                return False
            if r.status_code == 404:
                return False
            if r.status_code != 200:
                log.debug("acr tags/list unexpected: http=%s body=%s", r.status_code, (r.text or "")[:200])
                return False
            try:
                data = r.json() or {}
                tags = data.get("tags") or []
                return str(tag_to_check) in set([str(t) for t in tags])
            except Exception:
                return False

        def _get_manifest_any(tag_to_fetch: str):
            manifest_url2 = f"https://{registry}/v2/{name}/manifests/{tag_to_fetch}"
            pull_scope2 = f"repository:{name}:pull"
            last = None
            for a in accept_fallbacks:
                headers2 = {"Accept": a} if a else {}
                # Prefer GET here for maximum compatibility (some registries 404 on HEAD)
                r = _request_with_token("GET", manifest_url2, scope=pull_scope2, headers=headers2)
                last = r
                if r.status_code == 200:
                    return r
                # 401 should have been handled by _request_with_token retry; treat as terminal
                if r.status_code == 401:
                    return r
            return last

        # 1) 获取 digest：优先 HEAD（更轻量），不行再 GET
        manifest_url = f"https://{registry}/v2/{name}/manifests/{tag}"
        headers = {"Accept": accept}
        pull_scope = f"repository:{name}:pull"

        resp = _request_with_token("HEAD", manifest_url, scope=pull_scope, headers=headers)
        if resp.status_code == 404:
            # Some registries return 404 for HEAD on manifests; verify via GET before concluding not-found.
            resp = _request_with_token("GET", manifest_url, scope=pull_scope, headers=headers)
        if resp.status_code == 401:
            log.warning("acr unauthorized to read manifest (check ACR_USERNAME/ACR_PASSWORD): %s", image)
            return
        if resp.status_code not in (200, 201, 202):
            # fallback to GET with multiple Accept types (some registries require schema1/OCI/manifest-list)
            resp = _get_manifest_any(tag)
        if resp.status_code == 404:
            # Double-check via tags/list: if tags exist but manifest still 404, likely Accept mismatch or registry behavior.
            if _repo_tags_exist(tag):
                log.warning("acr tag exists but manifest not found (accept mismatch?): %s", image)
            else:
                log.info("acr image not found (already deleted?): %s", image)
            return
        if resp.status_code != 200:
            log.warning("failed to get manifest for %s: %s %s", image, resp.status_code, (resp.text or "")[:200])
            return

        digest = resp.headers.get("Docker-Content-Digest")
        if not digest:
            log.warning("no digest in manifest response for %s", image)
            return

        # 2) DELETE manifest（需要 delete scope）
        delete_url = f"https://{registry}/v2/{name}/manifests/{digest}"
        # Some registries require push permission to delete; request a wider scope.
        delete_scope = f"repository:{name}:pull,push,delete"
        del_resp = _request_with_token("DELETE", delete_url, scope=delete_scope)
        if del_resp.status_code in (200, 202, 204):
            log.info("acr image deleted: %s (digest=%s)", image, digest[:20])
        else:
            log.warning(
                "failed to delete acr image %s: %s %s",
                image,
                del_resp.status_code,
                (del_resp.text or "")[:200],
            )
    except Exception as e:
        log.warning("acr delete failed for %s: %s", image, e)


