"""Pull requests. GhForge uses Marco's gh; LocalForge (ARK_FORGE=local) keeps PRs in a json file
inside the repo's .git and squash-merges with plain git, for tests and offline sandboxes."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import gitops, paths


class ForgeError(Exception):
    pass


GH_TIMEOUT = 120  # seconds per gh call
_PR_URL = re.compile(r"https://[^/\s]+/[^/\s]+/[^/\s]+/pull/([0-9]+)")
_FULL_SHA = re.compile(r"[0-9a-f]{40,64}")


def _number(number) -> int:
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ForgeError(f"not a PR number: {number!r}")
    return number


def _check_mergeable(v: dict, head_sha) -> None:
    """The one rule both forges share: only an open PR into a plain ark/* branch, at exactly the
    head the caller checked. The default branch is never reachable through merge."""
    try:
        gitops._ark_only(v["base"])
    except gitops.GitError as e:
        raise ForgeError(f"refusing to merge: {e}") from None
    if v["state"] != "OPEN":
        raise ForgeError(f"refusing to merge: the PR is {v['state']}")
    if not isinstance(head_sha, str) or not _FULL_SHA.fullmatch(head_sha):
        raise ForgeError(f"refusing to merge: head_sha must be a full sha, not {head_sha!r}")
    if v["head_sha"] != head_sha:
        raise ForgeError("head moved")


class GhForge:
    def _gh(self, repo, *args, input: str | None = None) -> str:
        """gh with stdin closed (or the body from a file), no prompts and a timeout; the process
        group is killed on timeout. Any failure to run it is a ForgeError."""
        env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}
        what = " ".join(args[:3])
        try:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, \
                    tempfile.TemporaryFile() as stdin:
                if input is not None:
                    stdin.write(input.encode("utf-8", "replace"))
                    stdin.flush()
                    stdin.seek(0)
                p = subprocess.Popen(["gh", *args], cwd=str(repo), stdin=stdin if input is not None
                                     else subprocess.DEVNULL, stdout=out, stderr=err, env=env,
                                     start_new_session=True)
                try:
                    p.wait(timeout=GH_TIMEOUT)
                except subprocess.TimeoutExpired:
                    gitops._kill(p)
                    raise ForgeError(f"gh {what}: timed out after {GH_TIMEOUT}s") from None
                stdout, stderr = gitops._text(out, "replace"), gitops._text(err, "replace")
        except (OSError, ValueError) as e:
            raise ForgeError(f"gh {what}: {e}") from e
        if p.returncode:
            raise ForgeError(f"gh {what}: {(stderr or stdout).strip()}")
        return stdout.strip()

    def create(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        lines = self._gh(repo, "pr", "create", "--base", base, "--head", head, "--title", title,
                         "--body-file", "-", input=body).splitlines()
        url = lines[-1].strip() if lines else ""
        m = _PR_URL.fullmatch(url)
        if not m:
            raise ForgeError(f"gh pr create printed no PR url: {url[:200]!r}")
        pr = {"number": int(m.group(1)), "url": url}
        v = self.view(repo, pr["number"])
        if (v["base"], v["head"]) != (base, head):
            raise ForgeError(f"gh pr create returned {url}, which is {v['head']} into {v['base']}, "
                             f"not {head} into {base}")
        return pr

    def view(self, repo, number: int) -> dict:
        raw = self._gh(repo, "pr", "view", str(_number(number)), "--json",
                       "baseRefName,headRefName,headRefOid,state,url")
        try:
            d = json.loads(raw)
            v = {"base": d["baseRefName"], "head": d["headRefName"], "head_sha": d["headRefOid"],
                 "state": d["state"], "url": d["url"]}
        except (ValueError, KeyError, TypeError) as e:
            raise ForgeError(f"gh pr view {number}: unreadable output ({type(e).__name__})") from None
        if not all(isinstance(x, str) and x for x in v.values()):
            raise ForgeError(f"gh pr view {number}: unexpected fields")
        return v

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        _check_mergeable(self.view(repo, number), head_sha)
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

    def _pr(self, repo, number) -> dict:
        prs = self.list(repo)
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(prs):
            raise ForgeError(f"no PR {number!r}")
        return prs[number - 1]

    def view(self, repo, number: int) -> dict:
        pr = self._pr(repo, number)
        return {"base": pr["base"], "head": pr["head"], "head_sha": gitops.remote_sha(repo, pr["head"]),
                "state": pr["state"], "url": pr["url"]}

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        prs = self.list(repo)
        _check_mergeable(self.view(repo, number), head_sha)
        pr = prs[number - 1]
        scratch = paths.worktrees_dir() / "_forge" / uuid.uuid4().hex
        try:
            gitops.fetch(repo)
            sha = gitops.squash_keep(repo, f"origin/{pr['base']}", f"origin/{pr['head']}", subject, scratch)
            if sha is None:
                raise ForgeError("merge conflict")
            gitops.push_branch(scratch, pr["base"])
        except gitops.GitError as e:
            raise ForgeError(f"merge failed: {e}") from e
        finally:
            try:
                gitops.remove_worktree(repo, scratch)
            except gitops.GitError:
                pass
        pr["state"] = "MERGED"
        self._save(repo, prs)


def get():
    return LocalForge() if os.environ.get("ARK_FORGE") == "local" else GhForge()
