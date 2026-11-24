#!/usr/bin/env python3
"""Pure-Python GitHub SSH probe, clone, and push-with-verification."""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path

try:
    import paramiko
    from dulwich.client import SSHGitClient, SSHVendor
    from dulwich.repo import Repo
except ImportError as exc:
    raise SystemExit(
        "Missing dependency. Install dulwich and paramiko into an isolated directory "
        "and add it to PYTHONPATH."
    ) from exc


GITHUB_HOST = "ssh.github.com"
GITHUB_PORT = 443
GITHUB_ED25519_FINGERPRINT = (
    "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
)


class GitHubHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        actual = "SHA256:" + base64.b64encode(
            hashlib.sha256(key.asbytes()).digest()
        ).decode().rstrip("=")
        if actual != GITHUB_ED25519_FINGERPRINT:
            raise paramiko.SSHException(
                "GitHub host key mismatch: "
                f"expected {GITHUB_ED25519_FINGERPRINT}, got {actual}"
            )


class ParamikoConnection:
    def __init__(self, client, channel):
        self.client = client
        self.channel = channel
        self.stderr = channel.makefile_stderr("rb")
        self.write = channel.sendall

    def read(self, size):
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.channel.recv(size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def can_read(self):
        return self.channel.recv_ready()

    def close(self):
        self.channel.close()
        self.client.close()


class ParamikoVendor(SSHVendor):
    def __init__(self, key_path: Path):
        self.key_path = key_path

    def run_command(
        self,
        host,
        command,
        username=None,
        port=None,
        password=None,
        key_filename=None,
        ssh_command=None,
        protocol_version=None,
    ):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(GitHubHostKeyPolicy())
        client.connect(
            GITHUB_HOST,
            port=GITHUB_PORT,
            username=username or "git",
            key_filename=str(self.key_path),
            look_for_keys=False,
            allow_agent=False,
            timeout=20,
            auth_timeout=20,
            banner_timeout=20,
        )
        transport = client.get_transport()
        if transport is None:
            client.close()
            raise paramiko.SSHException("SSH transport was not created")
        channel = transport.open_session(timeout=20)
        channel.exec_command(command.decode("utf-8") if isinstance(command, bytes) else command)
        return ParamikoConnection(client, channel)


import re
from dulwich.graph import can_fast_forward
from dulwich.objects import Commit
from dulwich.refs import check_ref_format

ORIGIN = "git@github.com:"


def repository(value):
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", value) or value.split("/")[1] in {".", ".."}:
        raise argparse.ArgumentTypeError("expected GitHub OWNER/REPO")
    return value.removesuffix(".git")


def branch_ref(value):
    ref = f"refs/heads/{value}".encode()
    if not value or not check_ref_format(ref):
        raise argparse.ArgumentTypeError("invalid branch")
    return ref


def describe(result, ref=None):
    if ref is not None:
        oid = result.refs.get(ref)
        print(f"{ref.decode()} {oid.decode() if oid else 'MISSING'}")
        return
    if b"HEAD" in result.symrefs:
        print("DEFAULT", result.symrefs[b"HEAD"].decode())
    for name, oid in sorted(result.refs.items()):
        print(name.decode(), oid.decode())
    if not result.refs:
        print("REMOTE_REFS=EMPTY")


def clone(client, remote, args):
    target = args.target.resolve()
    if target.exists():
        raise SystemExit("Clone target already exists")
    refs = client.get_refs(remote)
    origin = ORIGIN + remote
    if refs.refs:
        branch = None
        if b"HEAD" not in refs.refs and b"HEAD" not in refs.symrefs:
            if b"refs/heads/main" in refs.refs:
                branch = "main"
            else:
                raise SystemExit("Remote has refs but no resolvable default branch")
        with client.clone(remote, str(target), origin_url=origin, branch=branch) as repo:
            print("CLONE", target, repo.head().decode())
        return
    target.mkdir()
    with Repo.init(target) as repo:
        repo.refs.set_symbolic_ref(b"HEAD", refs.symrefs.get(b"HEAD", b"refs/heads/main"))
        config = repo.get_config()
        config.set((b"remote", b"origin"), b"url", origin.encode())
        config.set((b"remote", b"origin"), b"fetch", b"+refs/heads/*:refs/remotes/origin/*")
        config.write_to_path()
    print("CLONE_EMPTY", target)


def push(client, remote, args):
    branch = args.branch
    with Repo(str(args.worktree.resolve())) as repo:
        config = repo.get_config_stack()
        section = (b"remote", b"origin")
        urls = list(config.get_multivar(section, b"pushurl")) or list(config.get_multivar(section, b"url"))
        accepted = {prefix + remote for prefix in (
            ORIGIN, "git@ssh.github.com:", "ssh://git@github.com/",
            "ssh://git@ssh.github.com:443/", "https://github.com/",
        )}
        accepted |= {url.removesuffix(".git") for url in accepted}
        if len(urls) != 1 or urls[0].decode().casefold() not in {url.casefold() for url in accepted}:
            raise SystemExit("origin push URL does not match the approved GitHub repository")
        approved = args.confirm_sha.lower().encode("ascii")
        if repo.refs[branch] != approved or not isinstance(repo[approved], Commit):
            raise SystemExit("Local branch no longer points to the approved commit")

        def update_refs(remote_refs):
            previous = remote_refs.get(branch)
            if previous is not None and previous != approved:
                if previous not in repo or not can_fast_forward(repo, previous, approved):
                    raise SystemExit("Cannot verify fast-forward. Fetch and review before retrying.")
            return {branch: approved}

        result = client.send_pack(remote.encode(), update_refs, repo.generate_pack_data)
        if result.ref_status and result.ref_status.get(branch):
            raise SystemExit(f"Push rejected: {result.ref_status[branch]}")
    observed = client.get_refs(remote).refs.get(branch)
    if observed != approved:
        raise SystemExit("Push result is unverified: remote branch does not match the approved SHA")
    print("PUSH_VERIFIED", branch.decode(), observed.decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--repo", type=repository, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("probe")
    commands.add_parser("clone").add_argument("--target", type=Path, required=True)
    commands.add_parser("verify").add_argument("--branch", type=branch_ref, required=True)
    push_parser = commands.add_parser("push")
    push_parser.add_argument("--worktree", type=Path, required=True)
    push_parser.add_argument("--branch", type=branch_ref, required=True)
    push_parser.add_argument("--confirm-sha", required=True)
    args = parser.parse_args()
    if args.command == "push" and not re.fullmatch(r"[a-fA-F0-9]{40}", args.confirm_sha):
        parser.error("--confirm-sha requires the full approved commit SHA")
    key = args.key.expanduser().resolve()
    if not key.is_file():
        parser.error("SSH private key file is unavailable")
    client = SSHGitClient(GITHUB_HOST, port=GITHUB_PORT, username="git", vendor=ParamikoVendor(key))
    remote = args.repo + ".git"
    if args.command in ("probe", "verify"):
        describe(client.get_refs(remote), getattr(args, "branch", None))
    elif args.command == "clone":
        clone(client, remote, args)
    else:
        push(client, remote, args)


if __name__ == "__main__":
    main()
