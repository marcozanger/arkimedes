"""Pull requests. GhForge uses Marco's gh; LocalForge (ARK_FORGE=local) keeps PRs in a json file
inside the repo's .git and squash-merges with plain git, for tests and offline sandboxes."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from . import gitops, paths


class ForgeError(Exception):
    pass


class GhForge:
    def _gh(self, repo, *args, input: str | None = None) -> str:
        r = subprocess.run(["gh", *args], cwd=str(repo), capture_output=True, text=True, input=input)
        if r.returncode:
            raise ForgeError(f"gh {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()}")
        return r.stdout.strip()

    def create(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        url = self._gh(repo, "pr", "create", "--base", base, "--head", head, "--title", title,
                       "--body-file", "-", input=body).splitlines()[-1].strip()
        return {"number": int(url.rstrip("/").rsplit("/", 1)[1]), "url": url}

    def view(self, repo, number: int) -> dict:
        d = json.loads(self._gh(repo, "pr", "view", str(number), "--json",
                                "baseRefName,headRefName,headRefOid,state,url"))
        return {"base": d["baseRefName"], "head": d["headRefName"], "head_sha": d["headRefOid"],
                "state": d["state"], "url": d["url"]}

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        self._gh(repo, "pr", "merge", str(number), "--squash", "--subject", subject, "--body", "",
                 "--match-head-commit", head_sha)


class LocalForge:
    def _file(self, repo) -> Path:
        return Path(repo) / ".git" / "ark-local-prs.json"

    def list(self, repo) -> list[dict]:
        f = self._file(repo)
        return json.loads(f.read_text()) if f.exists() else []

    def _save(self, repo, prs: list[dict]) -> None:
        self._file(repo).write_text(json.dumps(prs, indent=2))

    def create(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        prs = self.list(repo)
        n = len(prs) + 1
        pr = {"number": n, "url": f"local://{Path(repo).name}/pull/{n}", "base": base, "head": head,
              "title": title, "body": body, "state": "OPEN"}
        self._save(repo, prs + [pr])
        return {"number": n, "url": pr["url"]}

    def view(self, repo, number: int) -> dict:
        pr = self.list(repo)[number - 1]
        return {"base": pr["base"], "head": pr["head"], "head_sha": gitops.remote_sha(repo, pr["head"]),
                "state": pr["state"], "url": pr["url"]}

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        prs = self.list(repo)
        pr = prs[number - 1]
        if gitops.remote_sha(repo, pr["head"]) != head_sha:
            raise ForgeError("head moved")
        gitops.fetch(repo)
        scratch = paths.worktrees_dir() / "_forge" / uuid.uuid4().hex
        try:
            sha = gitops.squash_keep(repo, f"origin/{pr['base']}", f"origin/{pr['head']}", subject, scratch)
            if sha is None:
                raise ForgeError("merge conflict")
            gitops.push_branch(scratch, pr["base"])
        finally:
            gitops.remove_worktree(repo, scratch)
        pr["state"] = "MERGED"
        self._save(repo, prs)


def get():
    return LocalForge() if os.environ.get("ARK_FORGE") == "local" else GhForge()
