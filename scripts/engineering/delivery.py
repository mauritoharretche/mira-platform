"""Reviewed local delivery; no bootstrap, settings-write or merge-execution path."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import sys
import time
import uuid


REPOSITORY = "mauritoharretche/mira-platform"
REPOSITORY_ID = 1447558426
MAINTAINER = "Mauro Harretche"
WORKFLOW = ".github/workflows/engineering-foundation.yml"
JOBS = ("backend", "frontend", "Toolchain (ubuntu-latest)", "Toolchain (macos-15)")
TOOL_PATH = "scripts/engineering/delivery.py"
POLICY_PATH = ".github/rulesets/protect-main.json"
APP_ID = 15368
MARKER = "MIRA_DELIVERY_IDENTITY="
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
PR_PARAMETERS = {
    "allowed_merge_methods": ["squash"], "required_approving_review_count": 0,
    "dismiss_stale_reviews_on_push": False, "require_code_owner_review": False,
    "require_last_push_approval": False, "required_review_thread_resolution": True,
    "required_reviewers": [], "require_extra_approval_for_unattributed_changes": True,
}
CHECK_PARAMETERS = {
    "strict_required_status_checks_policy": True, "do_not_enforce_on_create": False,
    "required_status_checks": [{"context": name, "integration_id": APP_ID} for name in JOBS],
}
POLICY = {
    "name": "protect-main", "target": "branch", "enforcement": "active", "bypass_actors": [],
    "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
    "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
              {"type": "pull_request", "parameters": PR_PARAMETERS},
              {"type": "required_status_checks", "parameters": CHECK_PARAMETERS}],
}


class Blocked(RuntimeError):
    """A safety gate failed. No automatic repair or write retry is permitted."""


class Pending(Blocked):
    """Read-only observation is incomplete; never a successful quality gate."""


def require(condition, message):
    if not condition:
        raise Blocked(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def decode_json(data):
    try:
        return json.loads(data, object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(Blocked("Non-finite JSON value")))
    except (ValueError, UnicodeError) as error:
        raise Blocked("Invalid JSON") from error


def command(args, *, cwd=None, data=None, allowed=(0,), timeout=30):
    try:
        result = subprocess.run(args, cwd=cwd, input=data, capture_output=True,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                                     "GH_PROMPT_DISABLED": "1"}, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Blocked("Command unavailable or timed out; inspect state before any manual retry") from error
    require(result.returncode in allowed, "Command failed: " + args[0] + " (output withheld; inspect locally)")
    return result.stdout


def safe_path(path):
    require(isinstance(path, str) and bool(path) and "\\" not in path, "Invalid path")
    parts = PurePosixPath(path).parts
    require(not path.startswith("/") and str(PurePosixPath(path)) == path
            and not any(p in (".", "..", ".git") for p in parts)
            and not any(ord(c) < 32 or ord(c) == 127 for c in path), "Unsafe path")
    require(not any(p.lower() in ("private", "internal") for p in parts), "Private path blocked")
    require(not any(p == ".env" or (p.startswith(".env.") and p != ".env.example") for p in parts), "Environment path blocked")
    require(not path.lower().endswith((".pdf", ".pem", ".key", ".p12", ".pfx", ".dump", ".bak", ".sqlite", ".sqlite3", ".db")),
            "Sensitive artifact path blocked")
    return path


def scan(data, label):
    require(len(data) <= 5_000_000 and b"\0" not in data, "Binary or oversized file: " + label)
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise Blocked("Non-UTF-8 file: " + label) from error
    patterns = (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}",
                r"\bAKIA[0-9A-Z]{16}\b", re.escape("/" + "Users/"), r"MIRA" + r"_[^\s]*\.pdf")
    require(not any(re.search(pattern, text) for pattern in patterns), "Sensitive content pattern: " + label)
    assignments = re.findall(r'''(?im)\b\w*(?:password|secret|token|api_key)\s*["']?\s*[:=]\s*["']([^"'\n]+)["']''', text)
    # Existing accepted negative-authentication fixture: this exact file/value
    # is intentionally incorrect input, not a credential. Do not skip the file.
    require(all(value.startswith(("fictional-", "test-only-", "ci-only-", "REPLACE_", "PLACEHOLDER_"))
                or (label == "apps/api/identity/tests/test_authentication.py" and value == "incorrect") for value in assignments),
            "Review credential-like assignment: " + label)
    if PurePosixPath(label).name == ".env.example":
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if re.search(r"PASSWORD|SECRET|TOKEN|KEY", key, re.I):
                require(not value or value.startswith(("REPLACE_", "PLACEHOLDER_")), "Example must contain placeholders only")
    return text


class Git:
    def __init__(self, root):
        require(not any(name in os.environ for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                                                        "GIT_ALTERNATE_OBJECT_DIRECTORIES")), "Git environment redirection blocked")
        self.root = Path(root).resolve()
        require(self.text("rev-parse", "--show-toplevel").strip() == str(self.root), "Run from repository root")

    def run(self, *args, data=None, allowed=(0,)):
        return command(["git", "--literal-pathspecs", "-c", "core.quotePath=false", "-C", str(self.root), *args], data=data, allowed=allowed)

    def text(self, *args):
        return self.run(*args).decode("utf-8")

    def ref(self, name):
        return self.text("rev-parse", "--verify", name).strip()

    def config(self, name):
        return self.run("config", "--get", name, allowed=(0, 1)).decode().strip()

    def remote(self):
        expected = {"git@github.com:" + REPOSITORY + ".git", "https://github.com/" + REPOSITORY + ".git"}
        for args in (("remote", "get-url", "--all", "origin"), ("remote", "get-url", "--push", "--all", "origin")):
            require(self.text(*args).splitlines() in ([url] for url in expected), "Unexpected origin destination")
        require(self.config("remote.origin.mirror") not in ("true", "yes", "1"), "Mirror push blocked")

    def entries(self, ref):
        result = {}
        for item in self.run("ls-tree", "-r", "-z", ref).split(b"\0"):
            if not item:
                continue
            header, name = item.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            path = safe_path(name.decode("utf-8"))
            require(kind == "blob" and mode in ("100644", "100755"), "Unsupported tree entry")
            data = self.run("cat-file", "blob", oid)
            scan(data, path)
            result[path] = {"mode": mode, "sha256": digest(data)}
        return result

    def identity(self, variable):
        value = self.text("var", variable).strip()
        match = re.fullmatch(r"(.+) <([^<>]+)> \d+ [+-]\d{4}", value)
        require(match is not None, "Invalid Git identity")
        return {"name": match[1], "email": match[2]}


def tree_digest(entries):
    return digest(canonical(entries))


def external(path, git):
    path = Path(path)
    require(path.name != ".env" and not path.name.startswith(".env."), "Environment artifacts blocked")
    require(not path.is_symlink(), "Symlinked external artifact")
    resolved = path.resolve()
    common = Path(git.text("rev-parse", "--git-common-dir").strip())
    common = (git.root / common).resolve()
    require(not resolved.is_relative_to(git.root) and not resolved.is_relative_to(common), "Artifacts must be outside repository")
    return resolved


def write_new(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def transformations(git, paths):
    require(git.config("core.autocrlf") in ("", "false"), "Ambiguous core.autocrlf")
    attributes = git.run("check-attr", "-z", "--stdin", "filter", "working-tree-encoding", "text", "eol", "diff",
                         data=b"".join(p.encode() + b"\0" for p in paths))
    values = attributes.split(b"\0")[:-1]
    require(len(values) == len(paths) * 15 and all(v == b"unspecified" for v in values[2::3]), "Unsupported Git attributes")


def candidate(git, branch, base, allowlist):
    require(SHA.fullmatch(base) is not None, "Invalid base SHA")
    require(re.fullmatch(r"(?:feat|fix|chore|docs|test|refactor)/[A-Za-z0-9._/-]+", branch)
            and ".." not in branch and not branch.endswith("/"), "Invalid feature branch")
    require(git.text("branch", "--show-current").strip() == branch, "Wrong branch")
    require(all(git.ref(ref) == base for ref in ("HEAD", "refs/heads/main", "refs/remotes/origin/main")), "Stale or divergent base")
    require(not git.run("diff", "--cached", "--name-only", "-z"), "Index must be empty")
    require(isinstance(allowlist, list) and allowlist and len(set(allowlist)) == len(allowlist), "Invalid allowlist")
    for path in allowlist:
        safe_path(path)
        # check-ignore does not accept Git's global literal-pathspec magic.
        ignored = subprocess.run(["git", "-C", str(git.root), "check-ignore", "-q", "--", path], capture_output=True,
                                 env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, timeout=30).returncode
        require(ignored == 1, "Ignored path blocked")
    before = git.entries(base)
    untracked = [safe_path(p.decode()) for p in git.run("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p]
    require(set(untracked) <= set(allowlist), "Unexpected untracked files")
    paths = sorted(set(before) | set(untracked))
    transformations(git, paths)
    after = {}
    for path in paths:
        file = git.root / path
        require(not any((git.root / Path(*PurePosixPath(path).parts[:i])).is_symlink()
                        for i in range(1, len(PurePosixPath(path).parts) + 1)), "Symlink traversal blocked")
        if not file.exists():
            continue
        require(file.is_file(), "Non-regular file")
        data = file.read_bytes()
        scan(data, path)
        after[path] = {"mode": "100755" if file.stat().st_mode & 0o111 else "100644", "sha256": digest(data)}
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    require(set(changed) == set(allowlist), "Changes do not exactly match allowlist")
    records = [{"path": path, "operation": "M" if path in before and path in after else "A" if path in after else "D",
                "before": before.get(path), "after": after.get(path)} for path in changed]
    flags = ("--no-ext-diff", "--no-textconv", "--no-color", "--full-index", "--binary", "--diff-algorithm=myers",
             "--no-indent-heuristic", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")
    patch = git.run("diff", *flags, base, "--", *[p for p in changed if p in before])
    for path in changed:
        if path not in before:
            patch += git.run("diff", "--no-index", *flags, "--", "/dev/null", path, allowed=(0, 1))
    require(len(re.findall(rb"(?m)^diff --git ", patch)) == len(changed), "Incomplete review patch")
    return records, tree_digest(after), digest(patch), patch


MANIFEST_KEYS = {"schema", "repository", "repository_id", "branch", "base", "files", "tree_sha256", "patch_sha256",
                 "tool_sha256", "policy_sha256", "author", "committer", "commit_message", "pr_title", "pr_body", "evidence"}


def validate_manifest(value):
    require(isinstance(value, dict) and set(value) == MANIFEST_KEYS, "Unexpected manifest schema")
    require(type(value["schema"]) is int and value["schema"] == 1, "Unsupported schema")
    require(value["repository"] == REPOSITORY and value["repository_id"] == REPOSITORY_ID, "Wrong repository")
    require(isinstance(value["files"], list) and value["files"], "Empty manifest")
    for field in ("tree_sha256", "patch_sha256", "tool_sha256", "policy_sha256"):
        require(isinstance(value[field], str) and DIGEST.fullmatch(value[field]), "Invalid digest")
    for record in value["files"]:
        require(isinstance(record, dict) and set(record) == {"path", "operation", "before", "after"}, "Invalid file record")
        safe_path(record["path"])
        require(record["operation"] in ("A", "M", "D"), "Invalid operation")
        for entry in (record["before"], record["after"]):
            require(entry is None or (isinstance(entry, dict) and set(entry) == {"mode", "sha256"}
                    and entry["mode"] in ("100644", "100755") and isinstance(entry["sha256"], str)
                    and DIGEST.fullmatch(entry["sha256"])), "Invalid entry")
    names = [r["path"] for r in value["files"]]
    require(names == sorted(set(names)), "Noncanonical file order")
    for field in ("commit_message", "pr_title", "pr_body", "evidence"):
        require(isinstance(value[field], str) and value[field].strip(), "Missing reviewed metadata")
        scan(value[field].encode(), field)
    for field in ("author", "committer"):
        require(isinstance(value[field], dict) and set(value[field]) == {"name", "email"}
                and all(isinstance(v, str) and v.strip() for v in value[field].values()), "Invalid author identity")
    return value


def load_manifest(path, git):
    raw = external(path, git).read_bytes()
    value = validate_manifest(decode_json(raw))
    require(raw == canonical(value), "Manifest is not canonical")
    return value, digest(raw)


def prepare(git, *, branch, base, allowlist, message, title, body, evidence, output):
    records, tree, patch_hash, patch = candidate(git, branch, base, allowlist)
    value = validate_manifest({
        "schema": 1, "repository": REPOSITORY, "repository_id": REPOSITORY_ID, "branch": branch, "base": base,
        "files": records, "tree_sha256": tree, "patch_sha256": patch_hash,
        "tool_sha256": digest(Path(__file__).read_bytes()), "policy_sha256": digest(canonical(POLICY)),
        "author": git.identity("GIT_AUTHOR_IDENT"), "committer": git.identity("GIT_COMMITTER_IDENT"),
        "commit_message": message.rstrip("\n"), "pr_title": title, "pr_body": body, "evidence": evidence,
    })
    directory = external(output, git)
    directory.mkdir(mode=0o700)
    write_new(directory / "manifest.json", canonical(value))
    write_new(directory / "review.patch", patch)
    write_new(directory / "review-summary.json", canonical({"manifest_sha256": digest(canonical(value)),
              "files": records, "tree_sha256": tree, "approval": "Not granted by this tool; external human approval required"}))
    return {"manifest": str(directory / "manifest.json"), "manifest_sha256": digest(canonical(value)), "files": len(records)}


class GitHub:
    def __init__(self):
        require(os.environ.get("GH_HOST", "github.com") == "github.com", "Unexpected GitHub host")
        self.deadline = None

    def timeout(self):
        remaining = 30 if self.deadline is None else self.deadline - time.monotonic()
        require(remaining > 0, "GitHub observation deadline exceeded")
        return min(30, remaining)

    def get(self, path):
        prefix = "repos/" + REPOSITORY
        require(path == prefix or path.startswith(prefix + "/"), "Unexpected GitHub destination")
        return decode_json(command(["gh", "api", "--hostname", "github.com", path], timeout=self.timeout()))

    def items(self, path, key=None):
        result = []
        for page in range(1, 11):
            response = self.get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            items = response[key] if key else response
            require(isinstance(items, list), "Unexpected paginated response")
            result.extend(items)
            if len(items) < 100:
                return result
        raise Blocked("Pagination limit exceeded")

    def logs(self, run_id, job_id):
        return command(["gh", "run", "view", str(run_id), "--repo", REPOSITORY, "--job", str(job_id), "--log"],
                       timeout=self.timeout()).decode()

    def create_pr(self, branch, title, body_file):
        url = command(["gh", "pr", "create", "--repo", REPOSITORY, "--head", branch, "--base", "main",
                       "--title", title, "--body-file", str(body_file)]).decode().strip()
        require(re.fullmatch(r"https://github\.com/mauritoharretche/mira-platform/pull/[1-9]\d*", url), "Ambiguous PR creation result")
        return url


def endpoint(suffix=""):
    return "repos/" + REPOSITORY + suffix


def verify_rules(gh):
    repository = gh.get(endpoint())
    require(repository["id"] == REPOSITORY_ID and repository["full_name"] == REPOSITORY, "Wrong GitHub repository")
    require(repository["allow_squash_merge"] is True and repository["allow_auto_merge"] is False
            and repository["delete_branch_on_merge"] is False, "Unsafe merge settings")
    rulesets = gh.items(endpoint("/rulesets?includes_parents=true"))
    match = [r for r in rulesets if r["name"] == "protect-main" and r["enforcement"] == "active"]
    require(len(match) == 1 and match[0]["source"] == REPOSITORY, "Active protect-main ruleset missing or ambiguous")
    live = gh.get(endpoint("/rulesets/" + str(match[0]["id"])))
    require(all(live.get(key) == POLICY[key] for key in ("name", "target", "enforcement", "bypass_actors", "conditions")),
            "Ruleset target, enforcement or bypass drift")
    expected = {r["type"]: r.get("parameters", {}) for r in POLICY["rules"]}
    configured = {r["type"]: r.get("parameters", {}) for r in live["rules"]}
    require(len(configured) == len(live["rules"]) and configured == expected, "Configured protections differ from reviewed policy")
    effective = gh.items(endpoint("/rules/branches/main"))
    require({r["type"] for r in effective} == set(expected) and len(effective) == len(expected),
            "Required effective protections missing or additional rules need review")
    require(all(r.get("parameters", {}) == expected[r["type"]] and r["ruleset_id"] == live["id"] for r in effective),
            "Effective check contexts, sources or strictness differ")
    return {"enforcement": "verified", "ruleset_id": live["id"], "jobs": list(JOBS), "integration_id": APP_ID}


def check_approval(path, git, manifest_hash):
    require(path is not None, "External explicit human approval record is required; a digest is not authorization")
    file = external(path, git)
    metadata = file.stat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid() and not metadata.st_mode & 0o077,
            "Approval record must be owner-only and regular")
    approval = decode_json(file.read_bytes())
    require(isinstance(approval, dict) and set(approval) == {"action", "approver", "manifest_sha256", "reference", "statement"}, "Invalid approval record")
    require(approval["action"] == "publish" and approval["approver"] == MAINTAINER and approval["manifest_sha256"] == manifest_hash,
            "External approval does not correspond to this revision")
    require(isinstance(approval["reference"], str) and approval["reference"].strip(), "External approval reference required")
    require(approval["statement"] == "I authorize publication of canonical manifest SHA-256 " + manifest_hash + ".",
            "Explicit digest-specific approval statement required")
    # This checks correspondence only. The trusted operator must verify that the
    # cited human approval genuinely exists; this file is not authentication.


def verify_staged(git, manifest):
    expected = {r["path"] for r in manifest["files"]}
    actual = {p.decode() for p in git.run("diff", "--cached", "--name-only", "-z").split(b"\0") if p}
    require(actual == expected, "Unexpected staged paths")
    entries = {}
    for item in git.run("ls-files", "--stage", "-z").split(b"\0"):
        if not item:
            continue
        header, name = item.split(b"\t", 1)
        mode, oid, stage = header.decode().split()
        path = safe_path(name.decode())
        require(stage == "0" and mode in ("100644", "100755"), "Conflicted or unsupported staged entry")
        data = git.run("cat-file", "blob", oid)
        scan(data, path)
        entries[path] = {"mode": mode, "sha256": digest(data)}
    require(tree_digest(entries) == manifest["tree_sha256"], "Staged tree differs from approved content")
    git.run("diff", "--cached", "--check")


def verify_commit(git, manifest, commit):
    require(git.text("show", "-s", "--format=%P", commit).strip() == manifest["base"], "Unexpected commit parent")
    require(tree_digest(git.entries(commit)) == manifest["tree_sha256"], "Committed tree differs from approved content")
    require(git.text("show", "-s", "--format=%B", commit).rstrip("\n") == manifest["commit_message"], "Commit message changed")
    require(git.text("show", "-s", "--format=%an%n%ae", commit).splitlines()
            == [manifest["author"]["name"], manifest["author"]["email"]], "Commit author changed")
    require(git.text("show", "-s", "--format=%cn%n%ce", commit).splitlines()
            == [manifest["committer"]["name"], manifest["committer"]["email"]], "Commit committer changed")
    require(not git.run("status", "--porcelain=v1", "--untracked-files=all"), "Hooks or concurrent work changed repository")


def publish(git, gh, manifest, approved_digest, *, execute=False, approval=None, directory=None):
    validate_manifest(manifest)
    require(digest(canonical(manifest)) == approved_digest, "Approved manifest digest mismatch")
    require(manifest["tool_sha256"] == digest(Path(__file__).read_bytes())
            and manifest["policy_sha256"] == digest(canonical(POLICY)), "Tool or policy identity changed")
    records, tree, patch_hash, unused = candidate(git, manifest["branch"], manifest["base"], [r["path"] for r in manifest["files"]])
    require(records == manifest["files"] and tree == manifest["tree_sha256"] and patch_hash == manifest["patch_sha256"], "Reviewed content changed")
    git.remote()
    require(gh.get(endpoint("/branches/main"))["commit"]["sha"] == manifest["base"], "Remote main advanced")
    require(not gh.items(endpoint("/pulls?state=open&head=mauritoharretche:" + manifest["branch"])), "PR already exists; inspect receipt")
    refs = git.text("ls-remote", "origin", "refs/heads/main", "refs/heads/" + manifest["branch"]).splitlines()
    require(refs == [manifest["base"] + "\trefs/heads/main"], "Remote base or feature ref changed")
    if not execute:
        return {"dry_run": True, "manifest_sha256": approved_digest, "files": len(records),
                "authorization": "Requires external human approval; no mutation performed"}
    # Mandatory real-operation gate. No parameter, local file or bootstrap flag
    # can replace live configured AND effective protection verification.
    verify_rules(gh)
    baseline_tool = git.run("show", manifest["base"] + ":" + TOOL_PATH)
    require(digest(baseline_tool) == manifest["tool_sha256"], "Bootstrap/self-modified tooling cannot publish itself")
    check_approval(approval, git, approved_digest)
    require(git.identity("GIT_AUTHOR_IDENT") == manifest["author"] and git.identity("GIT_COMMITTER_IDENT") == manifest["committer"], "Git identity changed")
    require(directory is not None, "External receipt directory required")
    output = external(directory, git)
    output.mkdir(mode=0o700)
    state = {"manifest_sha256": approved_digest, "base": manifest["base"], "branch": manifest["branch"]}

    def record(phase):
        state["phase"] = phase
        write_new(output / (phase + ".json"), canonical(state))

    try:
        record("staging")
        git.run("add", "--", *[r["path"] for r in records])
        verify_staged(git, manifest)
        write_new(output / "staged.patch", git.run("diff", "--cached", "--no-ext-diff", "--no-textconv", "--full-index", "--binary"))
        require(not git.run("diff", "--name-only") and not git.run("ls-files", "--others", "--exclude-standard"),
                "Working content changed during staging")
        require(git.ref("HEAD") == manifest["base"], "HEAD changed during staging")
        record("committing")
        git.run("commit", "--cleanup=verbatim", "--file=-", data=(manifest["commit_message"] + "\n").encode())
        state["commit"] = git.ref("HEAD")
        verify_commit(git, manifest, state["commit"])
        state["tree"] = git.ref("HEAD^{tree}")
        record("committed")
        verify_rules(gh)
        require(gh.get(endpoint("/branches/main"))["commit"]["sha"] == manifest["base"], "Main changed before push")
        require(git.text("ls-remote", "origin", "refs/heads/" + manifest["branch"]).strip() == "", "Unexpected remote feature ref")
        record("pushing")
        git.run("push", "origin", state["commit"] + ":refs/heads/" + manifest["branch"])
        require(git.text("ls-remote", "origin", "refs/heads/" + manifest["branch"]).strip()
                == state["commit"] + "\trefs/heads/" + manifest["branch"], "Remote publication identity mismatch")
        record("pushed")
        body_file = output / "pr-body.md"
        write_new(body_file, manifest["pr_body"].encode())
        record("creating-pr")
        state["pr_url"] = gh.create_pr(manifest["branch"], manifest["pr_title"], body_file)
        number = int(state["pr_url"].rsplit("/", 1)[1])
        pr = verify_pr(gh, number, state["commit"], manifest["base"])
        require(pr["title"] == manifest["pr_title"] and pr["body"] == manifest["pr_body"], "Published PR metadata changed")
        record("published")
        return state
    except Exception:
        state["observed_local_head"] = git.ref("HEAD")
        record("stopped")
        raise


def verify_pr(gh, number, head, base, *, merged=False):
    require(type(number) is int and number > 0 and SHA.fullmatch(head) and SHA.fullmatch(base), "Invalid PR identity")
    pr = gh.get(endpoint("/pulls/" + str(number)))
    require(pr["number"] == number and pr["head"]["sha"] == head and pr["head"]["repo"]["id"] == REPOSITORY_ID
            and pr["base"]["ref"] == "main" and pr["base"]["repo"]["id"] == REPOSITORY_ID, "PR revision or repository changed")
    require(pr["auto_merge"] is None and pr["draft"] is False, "Draft or auto-merge PR blocked")
    if merged:
        require(pr["merged"] is True and pr["state"] == "closed", "PR is not merged")
    else:
        require(pr["state"] == "open" and pr["merged"] is False and pr["base"]["sha"] == base, "PR base or state changed")
        require(gh.get(endpoint("/branches/main"))["commit"]["sha"] == base, "Main advanced")
    return pr


def checkout_identity(logs):
    identities = []
    for line in logs.splitlines():
        if MARKER not in line:
            continue
        value = line.split(MARKER, 1)[1]
        if value.startswith("{"):
            identities.append(decode_json(value))
    require(len(identities) == 1, "Missing or ambiguous checkout evidence")
    return identities[0]


def verify_run(gh, *, head, event, base, number=None, tree=None, checkout=None):
    runs = gh.items(endpoint("/actions/runs?head_sha=" + head + "&event=" + event), "workflow_runs")
    runs = [r for r in runs if r["path"] == WORKFLOW]
    if not runs:
        raise Pending("Matching workflow run not yet visible")
    run = max(runs, key=lambda r: r["id"])
    require(type(run["run_attempt"]) is int and run["run_attempt"] > 0, "Invalid run attempt")
    require(run["head_sha"] == head and run["event"] == event
            and (event != "push" or run["head_branch"] == "main"), "Wrong workflow identity")
    history = gh.items(endpoint("/actions/runs/" + str(run["id"]) + "/jobs?filter=all"), "jobs")
    require(all(type(j["run_attempt"]) is int and j["run_attempt"] > 0 for j in history), "Invalid job attempt")
    require(all(j["name"] in JOBS and j["run_id"] == run["id"] and j["head_sha"] == head for j in history), "Unexpected job identity")
    require(len({(j["name"], j["run_attempt"]) for j in history}) == len(history), "Ambiguous job attempt identities")
    latest = {}
    for job in history:
        key = (job["run_attempt"], job["id"])
        if job["name"] not in latest or key > (latest[job["name"]]["run_attempt"], latest[job["name"]]["id"]):
            latest[job["name"]] = job
    require(all(j["run_attempt"] <= run["run_attempt"] for j in history), "Attempt metadata mismatch")
    if any(j["status"] == "completed" and j["conclusion"] != "success" for j in latest.values()):
        raise Blocked("Required CI job failed, cancelled, skipped or neutral")
    if run["status"] != "completed" or set(latest) != set(JOBS) or any(j["status"] != "completed" for j in latest.values()):
        raise Pending("Required CI jobs are missing or pending")
    require(run["conclusion"] == "success", "Workflow did not succeed")
    checks = gh.items(endpoint("/commits/" + head + "/check-runs?filter=latest"), "check_runs")
    require(len(checks) == 4 and {c["name"] for c in checks} == set(JOBS), "Missing or ambiguous required checks")
    require(all(c["app"]["id"] == APP_ID and c["app"]["slug"] == "github-actions"
                and c["head_sha"] == head and c["status"] == "completed" and c["conclusion"] == "success"
                and c["check_suite"]["id"] == run["check_suite_id"]
                and f"/actions/runs/{run['id']}/" in c["details_url"] for c in checks), "Wrong CI source, suite or conclusion")
    report = []
    for name in JOBS:
        job = latest[name]
        originals = [j for j in history if j["name"] == name and j["started_at"] == job["started_at"]
                     and j["completed_at"] == job["completed_at"] and j["runner_id"] == job["runner_id"]
                     and j["steps"] == job["steps"] and j["conclusion"] == "success"]
        original = min(originals, key=lambda j: (j["run_attempt"], j["id"]))
        identity = checkout_identity(gh.logs(run["id"], original["id"]))
        require(identity == {"repository": REPOSITORY, "run_id": str(run["id"]), "run_attempt": str(original["run_attempt"]),
                "event": event, "source": head, "base": base if event == "pull_request" else "",
                "pr": str(number) if number is not None else "", "checkout": checkout or head,
                "parents": [base, head] if event == "pull_request" else [base], "tree": tree}, "Checkout revision evidence mismatch")
        report.append({"name": name, "job_id": job["id"], "record_attempt": job["run_attempt"],
                       "execution_attempt": original["run_attempt"], "conclusion": "success"})
    return {"run_id": run["id"], "run_attempt": run["run_attempt"], "head": head, "url": run["html_url"], "jobs": report}


def observe(gh, operation, *, watch=False, timeout=600):
    require(type(timeout) is int and 1 <= timeout <= 600, "Observation timeout must be 1..600 seconds")
    gh.deadline = time.monotonic() + timeout
    while True:
        try:
            return operation()
        except Pending:
            remaining = gh.deadline - time.monotonic()
            if not watch or remaining <= 0:
                raise
            print("CI pending; read-only observation continues within deadline", file=sys.stderr)
            time.sleep(min(15, remaining))


def ci(gh, number, head, base):
    pr = verify_pr(gh, number, head, base)
    if pr["mergeable"] is None:
        raise Pending("GitHub is calculating mergeability")
    require(pr["mergeable"] is True, "Merge conflict")
    commit = gh.get(endpoint("/git/commits/" + head))
    require([p["sha"] for p in commit["parents"]] == [base], "Expected one atomic commit on approved base")
    result = verify_run(gh, head=head, event="pull_request", base=base, number=number,
                        tree=commit["tree"]["sha"], checkout=pr["merge_commit_sha"])
    verify_pr(gh, number, head, base)
    return result


def merge_preview(git, gh, number, head, manifest):
    verify_rules(gh)
    verify_commit(git, manifest, head)
    result = ci(gh, number, head, manifest["base"])
    pr = verify_pr(gh, number, head, manifest["base"])
    require(pr["head"]["ref"] == manifest["branch"], "PR source branch changed")
    require(pr["mergeable_state"] == "clean", "PR is not immediately mergeable")
    result["command"] = shlex.join(["gh", "pr", "merge", str(number), "--repo", REPOSITORY,
                                    "--squash", "--match-head-commit", head])
    result["approval"] = "Separate explicit human approval of this PR, exact head and current main is required before running this command"
    return result


def post_merge(git, gh, number, head, manifest):
    verify_commit(git, manifest, head)
    pr = verify_pr(gh, number, head, manifest["base"], merged=True)
    merged = pr["merge_commit_sha"]
    commit = gh.get(endpoint("/git/commits/" + merged))
    source = gh.get(endpoint("/git/commits/" + head))
    require([p["sha"] for p in commit["parents"]] == [manifest["base"]]
            and commit["tree"]["sha"] == source["tree"]["sha"], "Unexpected merged parent or tree")
    require(gh.get(endpoint("/branches/main"))["commit"]["sha"] == merged, "Main advanced beyond reviewed merge")
    result = verify_run(gh, head=merged, event="push", base=manifest["base"], tree=commit["tree"]["sha"])
    require(gh.get(endpoint("/branches/main"))["commit"]["sha"] == merged, "Main advanced during verification")
    return {"pr": number, "merged_sha": merged, "parent": manifest["base"], "tree": commit["tree"]["sha"], "ci": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    commands = parser.add_subparsers(dest="operation", required=True)
    prep = commands.add_parser("prepare")
    for flag in ("branch", "base", "allowlist", "commit-message", "pr-title", "pr-body", "evidence", "out-dir"):
        prep.add_argument("--" + flag, required=True)
    pub = commands.add_parser("publish")
    pub.add_argument("--manifest", required=True)
    pub.add_argument("--approved-manifest-sha256", required=True)
    pub.add_argument("--execute", action="store_true")
    pub.add_argument("--approval-record")
    pub.add_argument("--out-dir")
    rule = commands.add_parser("rules")
    rule.add_argument("--policy")
    check = commands.add_parser("ci")
    check.add_argument("--pr", type=int, required=True)
    check.add_argument("--expected-head", required=True)
    check.add_argument("--expected-base", required=True)
    for name in ("merge-preview", "post-merge"):
        sub = commands.add_parser(name)
        sub.add_argument("--pr", type=int, required=True)
        sub.add_argument("--expected-head", required=True)
        sub.add_argument("--manifest", required=True)
    for sub in (check, commands.choices["post-merge"]):
        sub.add_argument("--watch", action="store_true")
        sub.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        git, gh = Git(args.root), GitHub()
        if args.operation == "prepare":
            read = lambda p: external(p, git).read_bytes()
            result = prepare(git, branch=args.branch, base=args.base, allowlist=decode_json(read(args.allowlist)),
                             message=read(args.commit_message).decode(), title=args.pr_title, body=read(args.pr_body).decode(),
                             evidence=read(args.evidence).decode(), output=args.out_dir)
        elif args.operation == "rules":
            if args.policy:
                require(decode_json(Path(args.policy).read_bytes()) == POLICY, "Local policy differs from trusted specification")
            result = verify_rules(gh)
        elif args.operation == "ci":
            result = observe(gh, lambda: ci(gh, args.pr, args.expected_head, args.expected_base), watch=args.watch, timeout=args.timeout)
        else:
            manifest, manifest_hash = load_manifest(args.manifest, git)
            if args.operation == "publish":
                result = publish(git, gh, manifest, args.approved_manifest_sha256, execute=args.execute,
                                 approval=args.approval_record, directory=args.out_dir)
            elif args.operation == "merge-preview":
                result = merge_preview(git, gh, args.pr, args.expected_head, manifest)
            else:
                result = observe(gh, lambda: post_merge(git, gh, args.pr, args.expected_head, manifest),
                                 watch=args.watch, timeout=args.timeout)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (Blocked, OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        print("BLOCKED: " + str(error) if isinstance(error, Blocked) else "BLOCKED: invalid input, filesystem or API response", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
