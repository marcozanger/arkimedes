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

from . import accounts, gitops, paths


class ForgeError(Exception):
    pass


GH_TIMEOUT = 120  # seconds per gh call
_PR_URL = re.compile(r"https://[^/\s]+/[^/\s]+/[^/\s]+/pull/([0-9]{1,18})")
_FULL_SHA = re.compile(r"[0-9a-f]{40,64}")


def _number(number) -> int:
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ForgeError(f"not a PR number: {number!r}")
    return number


def _pr_ref(url) -> dict:
    m = _PR_URL.fullmatch(url) if isinstance(url, str) else None
    if not m:
        raise ForgeError(f"not a PR url: {str(url)[:200]!r}")
    return {"number": _number(int(m.group(1))), "url": url}


def _open_for(prs: list[dict], *, base: str, head: str) -> dict | None:
    """The rule both forges share for find-or-create: the one open PR of this head into this base,
    or None. Merged and closed PRs don't count; an open PR of this head into another base is an
    error, because nothing here can tell which of the two the caller means."""
    mine = [p for p in prs if p["head"] == head and p["state"] == "OPEN"]
    for p in mine:
        if p["base"] != base:
            raise ForgeError(f"{head} already has an open PR into {p['base']}, not {base}: {p['url']}")
    if len(mine) > 1:
        raise ForgeError(f"{head} has {len(mine)} open PRs into {base}")
    return mine[0] if mine else None


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
        group is killed on timeout. It runs as the repo's gh_user when an accounts rule names one.
        Any failure to run it is a ForgeError."""
        what = " ".join(args[:3])
        env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}
        name = paths.repo_name(repo)
        if name:
            try:
                env.update(accounts.gh_env(name))
            except accounts.AccountError as e:
                raise ForgeError(f"gh {what}: {e}") from None
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

    def _find_open(self, repo, base: str, head: str) -> dict | None:
        raw = self._gh(repo, "pr", "list", "--head", head, "--state", "open", "--json",
                       "url,baseRefName,headRefName,state,isCrossRepository")
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise TypeError("not a list")
            prs = [{"url": d["url"], "base": d["baseRefName"], "head": d["headRefName"], "state": d["state"]}
                   for d in rows if not d["isCrossRepository"]]   # a fork's branch can share the name
        except (ValueError, KeyError, TypeError, RecursionError) as e:
            raise ForgeError(f"gh pr list: unreadable output ({type(e).__name__})") from None
        found = _open_for(prs, base=base, head=head)
        return _pr_ref(found["url"]) if found else None

    def create(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        """Find-or-create, so a run that lost the PR's number can always get it back: a crash before
        the state was saved, or gh dying after GitHub already made the PR. A lookup that fails stops
        here: without an answer there is nothing safe to create."""
        pr = self._find_open(repo, base, head)
        if pr:
            return pr
        try:
            return self._create_new(repo, base=base, head=head, title=title, body=body)
        except ForgeError:
            try:
                pr = self._find_open(repo, base, head)
            except ForgeError:
                pr = None   # the next call looks again; the create's error says more
            if not pr:
                raise
            return pr

    def _create_new(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        lines = self._gh(repo, "pr", "create", "--base", base, "--head", head, "--title", title,
                         "--body-file", "-", input=body).splitlines()
        pr = _pr_ref(lines[-1].strip() if lines else "")
        url = pr["url"]
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
        except (ValueError, KeyError, TypeError, RecursionError) as e:
            raise ForgeError(f"gh pr view {number}: unreadable output ({type(e).__name__})") from None
        if not all(isinstance(x, str) and x for x in v.values()):
            raise ForgeError(f"gh pr view {number}: unexpected fields")
        return v

    def open_by_head(self, repo, head: str) -> list[dict]:
        """The open PRs of this repo whose head is `head`, into any base: [{number, url, base}]."""
        raw = self._gh(repo, "pr", "list", "--head", head, "--state", "open", "--json",
                       "url,number,baseRefName,isCrossRepository")
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise TypeError("not a list")
            out = []
            for d in rows:
                if d["isCrossRepository"]:   # a fork's branch can share the name
                    continue
                pr = _pr_ref(d["url"])
                if pr["number"] != _number(d["number"]) or not isinstance(d["baseRefName"], str):
                    raise TypeError("fields do not agree")
                out.append({"number": pr["number"], "url": pr["url"], "base": d["baseRefName"]})
        except (ValueError, KeyError, TypeError, RecursionError, ForgeError) as e:
            raise ForgeError(f"gh pr list: unreadable output ({type(e).__name__})") from None
        return out

    def close(self, repo, number: int, comment: str = "") -> None:
        """Close the PR, leaving `comment` on it when there is one. The comment goes as an argument: `gh pr close`
        reads no file."""
        self._gh(repo, "pr", "close", str(_number(number)), *(("--comment", comment) if comment else ()))

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        _check_mergeable(self.view(repo, number), head_sha)
        self._gh(repo, "pr", "merge", str(number), "--squash", "--subject", subject, "--body", "",
                 "--match-head-commit", head_sha)


class LocalForge:
    def _file(self, repo) -> Path:
        return Path(repo) / ".git" / "ark-local-prs.json"

    def list(self, repo) -> list[dict]:
        f = self._file(repo)
        if not f.exists():
            return []
        try:
            prs = json.loads(f.read_text())
        except (OSError, ValueError, RecursionError) as e:
            raise ForgeError(f"unreadable {f}: {e}") from e
        fields = ("number", "url", "base", "head", "state")
        if not isinstance(prs, list) or not all(isinstance(p, dict) and all(k in p for k in fields) for p in prs):
            raise ForgeError(f"unreadable {f}: not a list of PRs")
        return prs

    def _save(self, repo, prs: list[dict]) -> None:
        self._file(repo).write_text(json.dumps(prs, indent=2))

    def create(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        prs = self.list(repo)
        found = _open_for(prs, base=base, head=head)
        if found:
            return {"number": found["number"], "url": found["url"]}
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
        # As on GitHub, a merged PR keeps the head it merged, whatever happens to the branch later.
        return {"base": pr["base"], "head": pr["head"],
                "head_sha": pr.get("head_sha") or gitops.remote_sha(repo, pr["head"]),
                "state": pr["state"], "url": pr["url"]}

    def open_by_head(self, repo, head: str) -> list[dict]:
        return [{"number": p["number"], "url": p["url"], "base": p["base"]}
                for p in self.list(repo) if p["head"] == head and p["state"] == "OPEN"]

    def close(self, repo, number: int, comment: str = "") -> None:
        prs = self.list(repo)
        pr = self._pr(repo, number)
        if pr["state"] != "OPEN":
            raise ForgeError(f"PR {number} is {pr['state']}, not open")
        pr = prs[number - 1]
        pr["state"] = "CLOSED"
        if comment:
            pr.setdefault("comments", []).append(comment)
        self._save(repo, prs)

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        prs = self.list(repo)
        _check_mergeable(self.view(repo, number), head_sha)
        pr = prs[number - 1]
        scratch = paths.worktrees_dir() / "_forge" / uuid.uuid4().hex
        try:
            gitops.fetch(repo)
            sha = gitops.squash_keep(repo, f"origin/{pr['base']}", head_sha, subject, scratch)
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
        pr.update(state="MERGED", head_sha=head_sha)
        self._save(repo, prs)


def get():
    return LocalForge() if os.environ.get("ARK_FORGE") == "local" else GhForge()
