"""No live GitHub requests: temporary repositories, bare remotes and fake API data."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[2] / "scripts/engineering/delivery.py"
SPEC = importlib.util.spec_from_file_location("mira_delivery", SOURCE)
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


class FakeGitHub(d.GitHub):
    def __init__(self, git, base, *, protected=True):
        super().__init__()
        self.git, self.base, self.protected = git, base, protected
        self.settings = {"id": d.REPOSITORY_ID, "full_name": d.REPOSITORY,
                         "allow_squash_merge": True, "allow_auto_merge": False, "delete_branch_on_merge": False}
        self.policy = {**copy.deepcopy(d.POLICY), "id": 7}
        self.effective = [{**copy.deepcopy(rule), "ruleset_id": 7} for rule in d.POLICY["rules"]]
        self.pr = None
        self.runs, self.jobs, self.checks, self.log_data, self.commits = [], [], [], {}, {}
        self.created = []
        self.fail_create = False

    def get(self, path):
        if path == d.endpoint():
            return copy.deepcopy(self.settings)
        if path == d.endpoint("/branches/main"):
            return {"commit": {"sha": self.base}}
        if path == d.endpoint("/rulesets/7"):
            return copy.deepcopy(self.policy)
        if path == d.endpoint("/pulls/9"):
            return copy.deepcopy(self.pr)
        if path.startswith(d.endpoint("/git/commits/")):
            return copy.deepcopy(self.commits[path.rsplit("/", 1)[1]])
        raise AssertionError("Unmocked GitHub GET: " + path)

    def items(self, path, key=None):
        if path.startswith(d.endpoint("/rulesets?")):
            return [{"name": "protect-main", "id": 7, "enforcement": "active", "source": d.REPOSITORY}]
        if path == d.endpoint("/rules/branches/main"):
            return copy.deepcopy(self.effective if self.protected else self.effective[:2])
        if path.startswith(d.endpoint("/pulls?")):
            return []
        if "/actions/runs?" in path:
            return copy.deepcopy(self.runs)
        if "/jobs?" in path:
            return copy.deepcopy(self.jobs)
        if "/check-runs?" in path:
            return copy.deepcopy(self.checks)
        raise AssertionError("Unmocked GitHub collection: " + path)

    def logs(self, run_id, job_id):
        return self.log_data[(run_id, job_id)]

    def create_pr(self, branch, title, body_file):
        self.created.append((branch, title, Path(body_file).read_text()))
        self.pr = self.pr_data(self.git.ref("HEAD"), self.base, branch)
        self.pr.update(title=title, body=Path(body_file).read_text())
        if self.fail_create:
            raise d.Blocked("Ambiguous PR response")
        return "https://github.com/mauritoharretche/mira-platform/pull/9"

    @staticmethod
    def pr_data(head, base, branch="feat/fictional-delivery"):
        return {"number": 9, "head": {"sha": head, "ref": branch, "repo": {"id": d.REPOSITORY_ID}},
                "base": {"sha": base, "ref": "main", "repo": {"id": d.REPOSITORY_ID}},
                "state": "open", "merged": False, "draft": False, "auto_merge": None,
                "mergeable": True, "mergeable_state": "clean", "merge_commit_sha": "e" * 40}

    def workflow(self, head, base, *, event="pull_request", tree="c" * 40, checkout="e" * 40):
        self.pr = self.pr or self.pr_data(head, base)
        self.commits[head] = {"parents": [{"sha": base}], "tree": {"sha": tree}}
        self.runs = [{"id": 101, "run_attempt": 1, "path": d.WORKFLOW, "event": event,
                      "head_sha": head, "head_branch": "main" if event == "push" else "feat/fictional-delivery",
                      "status": "completed", "conclusion": "success", "check_suite_id": 501,
                      "html_url": "https://github.com/mauritoharretche/mira-platform/actions/runs/101"}]
        self.jobs, self.checks, self.log_data = [], [], {}
        for index, name in enumerate(d.JOBS):
            identifier = 201 + index
            self.jobs.append({"id": identifier, "name": name, "run_id": 101, "run_attempt": 1,
                              "head_sha": head, "status": "completed", "conclusion": "success",
                              "started_at": "fictional-start", "completed_at": "fictional-end",
                              "runner_id": 100 + index, "steps": [{"name": "Record revision identity", "conclusion": "success"}]})
            self.checks.append({"name": name, "head_sha": head, "status": "completed", "conclusion": "success",
                                "app": {"id": d.APP_ID, "slug": "github-actions"}, "check_suite": {"id": 501},
                                "details_url": f"https://github.com/{d.REPOSITORY}/actions/runs/101/job/{identifier}"})
            identity = {"repository": d.REPOSITORY, "run_id": "101", "run_attempt": "1", "event": event,
                        "source": head, "base": base if event == "pull_request" else "",
                        "pr": "9" if event == "pull_request" else "", "checkout": checkout,
                        "parents": [base, head] if event == "pull_request" else [base], "tree": tree}
            self.log_data[(101, identifier)] = "fictional\tstep\t" + d.MARKER + d.canonical(identity).decode()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        isolated = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_AUTHOR_NAME": "Fictional Maintainer", "GIT_AUTHOR_EMAIL": "fictional@example.invalid",
                     "GIT_COMMITTER_NAME": "Fictional Maintainer", "GIT_COMMITTER_EMAIL": "fictional@example.invalid"})
        isolated.start()
        self.addCleanup(isolated.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="mira-delivery-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / "work"
        self.root.mkdir()
        self.remote = self.directory / "remote.git"
        self.call("git", "init", "--bare", "--initial-branch=main", str(self.remote))
        self.call("git", "init", "-b", "main", str(self.root))
        self.call("git", "-C", str(self.root), "config", "user.name", "Fictional Maintainer")
        self.call("git", "-C", str(self.root), "config", "user.email", "fictional@example.invalid")
        self.call("git", "-C", str(self.root), "config", "core.hooksPath", ".git/hooks")
        self.call("git", "-C", str(self.root), "config", "core.autocrlf", "false")
        self.call("git", "-C", str(self.root), "config", "commit.gpgsign", "false")
        self.write(".gitignore", "private/\n.env\n")
        self.write(d.TOOL_PATH, SOURCE.read_text())
        self.write(d.POLICY_PATH, d.canonical(d.POLICY).decode())
        self.write("sample.txt", "original\n")
        self.write("keep.txt", "kept\n")
        self.call("git", "-C", str(self.root), "add", "--", ".gitignore", d.TOOL_PATH, d.POLICY_PATH, "sample.txt", "keep.txt")
        self.call("git", "-C", str(self.root), "commit", "-m", "test: fictional base")
        self.call("git", "-C", str(self.root), "remote", "add", "origin", str(self.remote))
        self.call("git", "-C", str(self.root), "push", "-u", "origin", "main")
        self.git = d.Git(self.root)
        self.base = self.git.ref("HEAD")
        self.git.run("switch", "-c", "feat/fictional-delivery")
        self.gh = FakeGitHub(self.git, self.base)
        # Only fixtures replace the production destination check: all pushes go
        # to this test's local bare remote, never the real repository.
        self.mock_remote = mock.patch.object(self.git, "remote")
        self.mock_remote.start()
        self.addCleanup(self.mock_remote.stop)

    def call(self, *args):
        return subprocess.run(args, check=True, capture_output=True).stdout

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        return file

    def manifest(self, allowlist=None, output="review"):
        self.write("sample.txt", "reviewed change\n")
        result = d.prepare(self.git, branch="feat/fictional-delivery", base=self.base,
                           allowlist=allowlist or ["sample.txt"], message="feat: fictional reviewed change",
                           title="Fictional reviewed change", body="Only fictional reviewed content.",
                           evidence="Observed fictional fixture checks passed.", output=self.directory / output)
        manifest, manifest_hash = d.load_manifest(result["manifest"], self.git)
        return manifest, manifest_hash

    def approval(self, manifest_hash):
        path = self.directory / "approval.json"
        d.write_new(path, d.canonical({"action": "publish", "approver": d.MAINTAINER,
                    "manifest_sha256": manifest_hash, "reference": "fictional-external-human-review-reference",
                    "statement": "I authorize publication of canonical manifest SHA-256 " + manifest_hash + "."}))
        return path

    def execute(self, manifest, manifest_hash):
        return d.publish(self.git, self.gh, manifest, manifest_hash, execute=True,
                         approval=self.approval(manifest_hash), directory=self.directory / "receipt")

    def snapshot(self):
        index = (self.root / ".git/index").read_bytes()
        return self.git.ref("HEAD"), hashlib.sha256(index).hexdigest(), self.git.run("status", "--porcelain=v1", "--untracked-files=all")

    def test_prepare_is_reproducible_and_never_writes_index_or_git_objects(self):
        self.write("sample.txt", "reviewed change\n")
        before = self.snapshot()
        objects = sorted(str(p.relative_to(self.root)) for p in (self.root / ".git/objects").rglob("*") if p.is_file())
        first, first_hash = self.manifest()
        second, second_hash = self.manifest(output="review-again")
        self.assertEqual((first, first_hash), (second, second_hash))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in (self.root / ".git/objects").rglob("*") if p.is_file()), objects)
        self.assertEqual((self.directory / "review/manifest.json").stat().st_mode & 0o777, 0o600)

    def test_patch_includes_new_files_unicode_spaces_and_explicit_deletions(self):
        self.write("new presentation café.txt", "fictional new content\n")
        (self.root / "keep.txt").unlink()
        manifest, unused = self.manifest(["sample.txt", "new presentation café.txt", "keep.txt"])
        self.assertEqual({r["path"]: r["operation"] for r in manifest["files"]},
                         {"sample.txt": "M", "new presentation café.txt": "A", "keep.txt": "D"})
        patch = (self.directory / "review/review.patch").read_bytes()
        self.assertEqual(patch.count(b"diff --git "), 3)
        self.assertIn(b"fictional new content", patch)

    def test_ignored_content_is_not_read_but_cannot_be_authorized(self):
        self.write("private/fictional.txt", "not for reading")
        self.write(".env", "not for reading")
        self.manifest()
        for name in ("private/fictional.txt", ".env"):
            with self.subTest(path=name), self.assertRaises(d.Blocked):
                d.candidate(self.git, "feat/fictional-delivery", self.base, ["sample.txt", name])

    def test_unexpected_changes_and_untracked_files_block_publication(self):
        manifest, value = self.manifest()
        for name in ("keep.txt", "extra.txt"):
            with self.subTest(path=name):
                before = (self.root / name).read_bytes() if (self.root / name).exists() else None
                self.write(name, "unreviewed\n")
                with self.assertRaises(d.Blocked):
                    d.publish(self.git, self.gh, manifest, value)
                if before is None:
                    (self.root / name).unlink()
                else:
                    (self.root / name).write_bytes(before)
        self.assertFalse(self.git.run("diff", "--cached", "--name-only"))

    def test_content_and_mode_tampering_invalidate_review(self):
        manifest, value = self.manifest()
        self.write("sample.txt", "unreviewed\n")
        with self.assertRaises(d.Blocked):
            d.publish(self.git, self.gh, manifest, value)
        self.write("sample.txt", "reviewed change\n").chmod(0o755)
        with self.assertRaises(d.Blocked):
            d.publish(self.git, self.gh, manifest, value)

    def test_digest_metadata_and_schema_tampering_are_rejected(self):
        manifest, value = self.manifest()
        for field, changed in (("pr_body", "unreviewed text"), ("commit_message", "unreviewed message"),
                                ("tree_sha256", "0" * 64), ("tool_sha256", "0" * 64)):
            with self.subTest(field=field):
                tampered = {**manifest, field: changed}
                with self.assertRaises(d.Blocked):
                    d.publish(self.git, self.gh, tampered, value)
        with self.assertRaises(d.Blocked):
            d.validate_manifest({**manifest, "bootstrap": True})
        with self.assertRaises(d.Blocked):
            d.decode_json('{"schema":1,"schema":1}')
        with self.assertRaises(d.Blocked):
            d.decode_json('{"schema":NaN}')

    def test_nonempty_index_wrong_branch_and_stale_base_are_rejected(self):
        self.write("sample.txt", "reviewed change\n")
        for branch, base in (("main", self.base), ("feat/fictional-delivery", "0" * 40)):
            with self.subTest(branch=branch, base=base), self.assertRaises(d.Blocked):
                d.candidate(self.git, branch, base, ["sample.txt"])
        self.git.run("add", "--", "sample.txt")
        with self.assertRaises(d.Blocked):
            d.candidate(self.git, "feat/fictional-delivery", self.base, ["sample.txt"])

    def test_unsupported_files_and_path_transformations_are_rejected(self):
        self.write("sample.txt", "reviewed change\n")
        for name in ("../escape", "/absolute", "x/../y", "x\ny", "docs/internal/x", "key.pem", "file.pdf", ".env.local"):
            with self.subTest(path=name), self.assertRaises(d.Blocked):
                d.safe_path(name)
        (self.root / "link").symlink_to(self.root / "keep.txt")
        with self.assertRaises(d.Blocked):
            d.candidate(self.git, "feat/fictional-delivery", self.base, ["sample.txt", "link"])
        (self.root / "link").unlink()
        (self.root / "binary").write_bytes(b"a\0b")
        with self.assertRaises(d.Blocked):
            d.candidate(self.git, "feat/fictional-delivery", self.base, ["sample.txt", "binary"])
        (self.root / "binary").unlink()
        self.write(".gitattributes", "sample.txt text\n")
        with self.assertRaises(d.Blocked):
            d.candidate(self.git, "feat/fictional-delivery", self.base, ["sample.txt", ".gitattributes"])

    def test_sensitive_content_is_blocked_without_echoing_it(self):
        for secret in ("gh" + "p_" + "x" * 36, "/" + "Users/fictional/internal", "pass" + "word=" + json.dumps("not-approved")):
            self.write("sample.txt", secret)
            with self.assertRaises(d.Blocked) as failure:
                d.candidate(self.git, "feat/fictional-delivery", self.base, ["sample.txt"])
            self.assertNotIn(secret, str(failure.exception))

    def test_dry_run_does_not_write_index_commit_remote_or_pr(self):
        manifest, value = self.manifest()
        before = self.snapshot()
        self.gh.protected = False
        result = d.publish(self.git, self.gh, manifest, value)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.git.text("ls-remote", "origin", "refs/heads/feat/fictional-delivery").strip())
        self.assertEqual(self.gh.created, [])

    def test_gate_a_real_execution_is_blocked_before_staging(self):
        manifest, value = self.manifest()
        before = self.snapshot()
        self.gh.protected = False
        with self.assertRaisesRegex(d.Blocked, "effective protections"):
            d.publish(self.git, self.gh, manifest, value, execute=True)
        self.assertEqual(self.snapshot(), before)

    def test_digest_alone_and_mismatched_external_approval_are_not_authorization(self):
        manifest, value = self.manifest()
        before = self.snapshot()
        with self.assertRaisesRegex(d.Blocked, "External explicit human approval"):
            d.publish(self.git, self.gh, manifest, value, execute=True)
        wrong = self.approval("0" * 64)
        with self.assertRaises(d.Blocked):
            d.publish(self.git, self.gh, manifest, value, execute=True, approval=wrong)
        self.assertEqual(self.snapshot(), before)

    def test_untrusted_self_modified_tool_cannot_publish(self):
        self.write(d.TOOL_PATH, "# unreviewed replacement\n")
        manifest, value = self.manifest(["sample.txt", d.TOOL_PATH])
        # Running tool is trusted, but candidate code differs; publishing the new
        # version through this old trusted tool remains possible after review.
        self.assertNotEqual(manifest["files"][1]["after"], manifest["files"][1]["before"])
        with mock.patch.object(d.Path, "read_bytes", autospec=True) as read:
            # The explicit tool digest check itself is exercised without allowing
            # any writer call; malformed metadata is never an approval shortcut.
            read.return_value = b"different running tool"
            with self.assertRaises(d.Blocked):
                d.publish(self.git, self.gh, manifest, value)

    def test_successful_publication_uses_one_commit_explicit_paths_and_fixed_sha_push(self):
        manifest, value = self.manifest()
        result = self.execute(manifest, value)
        self.assertEqual(result["phase"], "published")
        self.assertEqual(result["pr_url"], "https://github.com/mauritoharretche/mira-platform/pull/9")
        self.assertEqual(self.git.text("rev-list", "--count", self.base + "..HEAD").strip(), "1")
        self.assertEqual(self.git.text("ls-remote", "origin", "refs/heads/feat/fictional-delivery").strip(),
                         result["commit"] + "\trefs/heads/feat/fictional-delivery")
        self.assertFalse(self.git.run("status", "--porcelain=v1", "--untracked-files=all"))
        self.assertEqual(d.tree_digest(self.git.entries("HEAD")), manifest["tree_sha256"])

    def hook(self, name, source):
        path = self.root / ".git/hooks" / name
        path.write_text("#!/bin/sh\n" + source)
        path.chmod(0o755)

    def test_commit_hook_cannot_silently_change_reviewed_tree(self):
        manifest, value = self.manifest()
        self.hook("pre-commit", "printf 'hook change\\n' > sample.txt\ngit add -- sample.txt\n")
        with self.assertRaisesRegex(d.Blocked, "Committed tree"):
            self.execute(manifest, value)
        self.assertNotEqual(self.git.ref("HEAD"), self.base)
        self.assertFalse(self.git.text("ls-remote", "origin", "refs/heads/feat/fictional-delivery").strip())
        self.assertTrue((self.directory / "receipt/stopped.json").exists())

    def test_post_commit_hook_dirty_worktree_prevents_push(self):
        manifest, value = self.manifest()
        self.hook("post-commit", "printf 'unreviewed\\n' > keep.txt\n")
        with self.assertRaisesRegex(d.Blocked, "Hooks or concurrent"):
            self.execute(manifest, value)
        self.assertFalse(self.git.text("ls-remote", "origin", "refs/heads/feat/fictional-delivery").strip())

    def test_commit_failure_preserves_index_and_does_not_attempt_push(self):
        manifest, value = self.manifest()
        self.hook("pre-commit", "exit 1\n")
        with self.assertRaises(d.Blocked):
            self.execute(manifest, value)
        self.assertEqual(self.git.ref("HEAD"), self.base)
        self.assertTrue(self.git.run("diff", "--cached", "--name-only"))
        self.assertEqual(self.gh.created, [])

    def test_ambiguous_push_stops_without_reset_or_second_push(self):
        manifest, value = self.manifest()
        original = self.git.run
        calls = []

        def ambiguous(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == "push":
                calls.append(args)
                raise d.Blocked("Ambiguous push response")
            return result

        with mock.patch.object(self.git, "run", side_effect=ambiguous):
            with self.assertRaises(d.Blocked):
                self.execute(manifest, value)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], self.git.ref("HEAD") + ":refs/heads/feat/fictional-delivery")
        self.assertTrue((self.directory / "receipt/pushing.json").exists())
        with self.assertRaises(d.Blocked):
            d.publish(self.git, self.gh, manifest, value, execute=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.gh.created, [])

    def test_ambiguous_pr_creation_preserves_pushed_commit_without_duplicate_retry(self):
        manifest, value = self.manifest()
        self.gh.fail_create = True
        with self.assertRaises(d.Blocked):
            self.execute(manifest, value)
        self.assertEqual(len(self.gh.created), 1)
        self.assertTrue((self.directory / "receipt/creating-pr.json").exists())
        self.assertTrue((self.directory / "receipt/stopped.json").exists())
        self.assertNotEqual(self.git.ref("HEAD"), self.base)

    def test_rule_validation_checks_effective_checks_sources_bypasses_and_settings(self):
        self.assertEqual(d.verify_rules(self.gh)["enforcement"], "verified")
        cases = (("settings", {**self.gh.settings, "allow_auto_merge": True}),
                 ("settings", {**self.gh.settings, "delete_branch_on_merge": True}),
                 ("policy", {**self.gh.policy, "bypass_actors": [{"actor_id": 1}]}),
                 ("effective", self.gh.effective[:2]))
        for attr, value in cases:
            original = getattr(self.gh, attr)
            with self.subTest(attr=attr), mock.patch.object(self.gh, attr, value), self.assertRaises(d.Blocked):
                d.verify_rules(self.gh)
            self.assertEqual(getattr(self.gh, attr), original)
        for field, value in (("integration_id", 999), ("context", "wrong")):
            effective = copy.deepcopy(self.gh.effective)
            effective[-1]["parameters"]["required_status_checks"][0][field] = value
            with mock.patch.object(self.gh, "effective", effective), self.assertRaises(d.Blocked):
                d.verify_rules(self.gh)

    def test_ci_requires_four_successful_correlated_jobs_and_checkout_evidence(self):
        head, base = "a" * 40, "b" * 40
        self.gh.base = base
        self.gh.workflow(head, base)
        result = d.ci(self.gh, 9, head, base)
        self.assertEqual(result["run_attempt"], 1)
        self.assertEqual({j["name"] for j in result["jobs"]}, set(d.JOBS))
        for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out"):
            self.gh.jobs[0]["conclusion"] = conclusion
            with self.subTest(conclusion=conclusion), self.assertRaises(d.Blocked):
                d.ci(self.gh, 9, head, base)

    def test_ci_rejects_pending_missing_wrong_workflow_and_wrong_check_sources(self):
        head, base = "a" * 40, "b" * 40
        self.gh.base = base
        for mutation in (lambda: self.gh.jobs.pop(), lambda: self.gh.jobs[0].update(status="queued", conclusion=None),
                         lambda: self.gh.runs[0].update(path="unrelated.yml"),
                         lambda: self.gh.checks[0]["app"].update(id=999),
                         lambda: self.gh.checks[0].update(check_suite={"id": 999}),
                         lambda: self.gh.checks[0].update(head_sha="f" * 40),
                         lambda: self.gh.log_data.update({(101, 201): "no revision evidence"})):
            self.gh.workflow(head, base)
            mutation()
            with self.assertRaises(d.Blocked):
                d.ci(self.gh, 9, head, base)

    def test_ci_rejects_changed_head_base_conflicts_and_ambiguous_checkout_identity(self):
        head, base = "a" * 40, "b" * 40
        self.gh.base = base
        self.gh.workflow(head, base)
        for change in ({"head": {**self.gh.pr["head"], "sha": "f" * 40}}, {"draft": True},
                       {"mergeable": False}, {"auto_merge": {"enabled": True}}):
            with mock.patch.object(self.gh, "pr", {**self.gh.pr, **change}), self.assertRaises(d.Blocked):
                d.ci(self.gh, 9, head, base)
        with mock.patch.object(self.gh, "base", "f" * 40), self.assertRaises(d.Blocked):
            d.ci(self.gh, 9, head, base)
        self.gh.log_data[(101, 201)] *= 2
        with self.assertRaises(d.Blocked):
            d.ci(self.gh, 9, head, base)

    def test_ci_distinguishes_carried_forward_successes_from_a_real_second_attempt(self):
        head, base = "a" * 40, "b" * 40
        self.gh.base = base
        self.gh.workflow(head, base)
        self.gh.runs[0]["run_attempt"] = 2
        original = copy.deepcopy(self.gh.jobs)
        for old in original:
            new = {**copy.deepcopy(old), "id": old["id"] + 1000, "run_attempt": 2}
            if old["name"] == "frontend":
                self.gh.jobs[1]["conclusion"] = "failure"
                new.update(started_at="fictional-retry-start", completed_at="fictional-retry-end", runner_id=999)
                identity = d.checkout_identity(self.gh.log_data[(101, old["id"])])
                identity["run_attempt"] = "2"
                self.gh.log_data[(101, new["id"])] = d.MARKER + d.canonical(identity).decode()
            self.gh.jobs.append(new)
        result = d.ci(self.gh, 9, head, base)
        for job in result["jobs"]:
            self.assertEqual(job["record_attempt"], 2)
            self.assertEqual(job["execution_attempt"], 2 if job["name"] == "frontend" else 1)

    def test_observation_has_a_hard_deadline_and_never_retries_writes(self):
        operation = mock.Mock(side_effect=d.Pending("pending"))
        with mock.patch.object(d.time, "monotonic", side_effect=[0, 0, 2]), mock.patch.object(d.time, "sleep") as sleep:
            with self.assertRaises(d.Pending):
                d.observe(self.gh, operation, watch=True, timeout=1)
        self.assertEqual(operation.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        with self.assertRaises(d.Blocked):
            d.observe(self.gh, operation, timeout=601)

    def test_merge_preview_never_merges_or_emits_branch_deletion_or_bypass_flags(self):
        manifest, value = self.manifest()
        result = self.execute(manifest, value)
        self.gh.workflow(result["commit"], self.base, tree=result["tree"])
        preview = d.merge_preview(self.git, self.gh, 9, result["commit"], manifest)
        self.assertIn("--match-head-commit " + result["commit"], preview["command"])
        for flag in ("--auto", "--admin", "--delete-branch", "--rebase"):
            self.assertNotIn(flag, preview["command"])
        self.assertFalse(self.gh.pr["merged"])

    def test_post_merge_verifies_squash_tree_parent_and_new_push_run(self):
        manifest, value = self.manifest()
        result = self.execute(manifest, value)
        head, merged = result["commit"], "d" * 40
        self.gh.pr.update(merged=True, state="closed", merge_commit_sha=merged)
        self.gh.base = merged
        self.gh.workflow(merged, manifest["base"], event="push", tree=result["tree"], checkout=merged)
        self.gh.commits[head] = {"parents": [{"sha": manifest["base"]}], "tree": {"sha": result["tree"]}}
        final = d.post_merge(self.git, self.gh, 9, head, manifest)
        self.assertEqual(final["merged_sha"], merged)
        self.gh.jobs[0]["conclusion"] = "failure"
        with self.assertRaises(d.Blocked):
            d.post_merge(self.git, self.gh, 9, head, manifest)
        self.gh.jobs[0]["conclusion"] = "success"
        self.gh.commits[merged]["parents"] = [{"sha": "f" * 40}]
        with self.assertRaises(d.Blocked):
            d.post_merge(self.git, self.gh, 9, head, manifest)

    def test_api_errors_and_permission_failure_do_not_trigger_writers(self):
        manifest, value = self.manifest()
        before = self.snapshot()
        with mock.patch.object(self.gh, "get", side_effect=d.Blocked("Permission denied")):
            with self.assertRaises(d.Blocked):
                d.publish(self.git, self.gh, manifest, value, execute=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.gh.created, [])

    def test_production_remote_validation_rejects_local_and_credentialed_destinations(self):
        self.mock_remote.stop()
        with self.assertRaises(d.Blocked):
            self.git.remote()
        self.git.run("remote", "set-url", "origin", "https://fictional@github.com/mauritoharretche/mira-platform.git")
        with self.assertRaises(d.Blocked):
            self.git.remote()

    def test_external_artifacts_and_approval_cannot_live_in_the_checkout(self):
        with self.assertRaises(d.Blocked):
            d.external(self.root / "review.json", self.git)
        path = self.directory / "symlink"
        path.symlink_to(self.root / "sample.txt")
        with self.assertRaises(d.Blocked):
            d.external(path, self.git)

    def test_cli_exposes_only_the_six_authorized_operations(self):
        with self.assertRaises(SystemExit) as error, mock.patch("sys.stderr"):
            d.main(["merge", "--execute"])
        self.assertEqual(error.exception.code, 2)

    def test_staged_extra_file_is_rejected(self):
        manifest, unused = self.manifest()
        self.git.run("add", "--", "sample.txt")
        self.write("extra.txt", "unreviewed\n")
        self.git.run("add", "--", "extra.txt")
        with self.assertRaises(d.Blocked):
            d.verify_staged(self.git, manifest)

    def test_staged_same_path_tampering_is_not_hidden_by_an_exact_file_count(self):
        manifest, unused = self.manifest()
        self.write("sample.txt", "tampered after review\n")
        self.git.run("add", "--", "sample.txt")
        with self.assertRaisesRegex(d.Blocked, "Staged tree"):
            d.verify_staged(self.git, manifest)

    def test_forced_tracked_environment_is_blocked(self):
        self.write(".env", "fictional fixture only\n")
        self.git.run("add", "-f", "--", ".env")
        self.git.run("commit", "-m", "test: fictional sensitive path")
        with self.assertRaises(d.Blocked):
            self.git.entries("HEAD")

    def test_submodule_tree_is_blocked_without_following_it(self):
        self.git.run("update-index", "--add", "--cacheinfo", "160000," + self.base + ",fixture-module")
        self.git.run("commit", "-m", "test: fictional submodule entry")
        with self.assertRaisesRegex(d.Blocked, "Unsupported tree entry"):
            self.git.entries("HEAD")

    def test_literal_pathspec_characters_publish_only_the_named_file(self):
        self.write("literal[1]*.txt", "fictional literal filename\n")
        manifest, value = self.manifest(["sample.txt", "literal[1]*.txt"])
        result = self.execute(manifest, value)
        self.assertEqual(result["phase"], "published")
        self.assertEqual(set(self.git.text("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()),
                         {"sample.txt", "literal[1]*.txt"})

    def test_mode_drift_is_detected_even_when_git_ignores_filesystem_modes(self):
        self.git.run("config", "core.filemode", "false")
        self.write("sample.txt", "reviewed change\n")
        (self.root / "keep.txt").chmod(0o755)
        with self.assertRaisesRegex(d.Blocked, "exactly match"):
            d.candidate(self.git, "feat/fictional-delivery", self.base, ["sample.txt"])

    def test_approval_must_be_explicit_owner_only_and_match_the_digest(self):
        for override in ({"approver": "fictional unauthorized actor"}, {"reference": ""},
                         {"statement": "approved"}, {"action": "merge"}):
            record = {"action": "publish", "approver": d.MAINTAINER, "manifest_sha256": "a" * 64,
                      "reference": "fictional external reference", "statement": "I authorize publication of canonical manifest SHA-256 " + "a" * 64 + "."}
            record.update(override)
            path = self.directory / ("approval-" + str(len(list(self.directory.glob('approval-*')))) + ".json")
            d.write_new(path, d.canonical(record))
            with self.assertRaises(d.Blocked):
                d.check_approval(path, self.git, "a" * 64)
        path = self.approval("a" * 64)
        path.chmod(0o644)
        with self.assertRaises(d.Blocked):
            d.check_approval(path, self.git, "a" * 64)

    def test_missing_baseline_tool_has_no_bootstrap_bypass(self):
        manifest, value = self.manifest()
        original = self.git.run
        writers = []

        def missing(*args, **kwargs):
            if args == ("show", self.base + ":" + d.TOOL_PATH):
                raise d.Blocked("Tool does not exist in baseline")
            if args[0] in ("add", "commit", "push"):
                writers.append(args)
            return original(*args, **kwargs)

        with mock.patch.object(self.git, "run", side_effect=missing), self.assertRaises(d.Blocked):
            self.execute(manifest, value)
        self.assertEqual(writers, [])

    def test_ambiguous_commit_response_preserves_actual_head_without_pushing(self):
        manifest, value = self.manifest()
        original = self.git.run

        def ambiguous(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == "commit":
                raise d.Blocked("Commit response lost")
            return result

        with mock.patch.object(self.git, "run", side_effect=ambiguous), self.assertRaises(d.Blocked):
            self.execute(manifest, value)
        self.assertNotEqual(self.git.ref("HEAD"), self.base)
        receipt = d.decode_json((self.directory / "receipt/stopped.json").read_bytes())
        self.assertEqual(receipt["observed_local_head"], self.git.ref("HEAD"))
        self.assertFalse(self.git.text("ls-remote", "origin", "refs/heads/feat/fictional-delivery").strip())

    def test_wrong_checkout_source_parents_pr_or_attempt_cannot_pass_ci(self):
        head, base = "a" * 40, "b" * 40
        self.gh.base = base
        for field, value in (("source", "f" * 40), ("checkout", "f" * 40), ("parents", [head, base]),
                             ("pr", "10"), ("run_attempt", "2"), ("run_id", "102"), ("tree", "f" * 40)):
            self.gh.workflow(head, base)
            identity = d.checkout_identity(self.gh.log_data[(101, 201)])
            identity[field] = value
            self.gh.log_data[(101, 201)] = d.MARKER + d.canonical(identity).decode()
            with self.subTest(field=field), self.assertRaises(d.Blocked):
                d.ci(self.gh, 9, head, base)

    def test_newer_failed_attempt_cannot_inherit_an_older_success(self):
        head, base = "a" * 40, "b" * 40
        self.gh.base = base
        self.gh.workflow(head, base)
        self.gh.runs[0].update(run_attempt=2, conclusion="failure")
        new = {**copy.deepcopy(self.gh.jobs[1]), "id": 1202, "run_attempt": 2, "conclusion": "failure"}
        self.gh.jobs.append(new)
        with self.assertRaises(d.Blocked):
            d.ci(self.gh, 9, head, base)

    def test_network_timeout_does_not_retry_a_command(self):
        with mock.patch.object(d.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 1)) as run:
            with self.assertRaises(d.Blocked):
                d.command(["gh", "api", "fictional"], timeout=1)
        self.assertEqual(run.call_count, 1)

    def test_pagination_is_complete_but_bounded(self):
        transport = d.GitHub()
        with mock.patch.object(transport, "get", side_effect=[[{}] * 100, [{}]]) as get:
            self.assertEqual(len(transport.items(d.endpoint("/rulesets"))), 101)
            self.assertEqual(get.call_count, 2)
        with mock.patch.object(transport, "get", return_value=[{}] * 100) as get:
            with self.assertRaises(d.Blocked):
                transport.items(d.endpoint("/rulesets"))
            self.assertEqual(get.call_count, 10)

    def test_git_environment_redirection_is_rejected_before_any_command(self):
        with mock.patch.dict(os.environ, {"GIT_INDEX_FILE": str(self.directory / "wrong-index")}), mock.patch.object(d, "command") as run:
            with self.assertRaises(d.Blocked):
                d.Git(self.root)
            run.assert_not_called()

    def test_noncanonical_manifest_and_external_environment_inputs_are_blocked(self):
        manifest, unused = self.manifest()
        path = self.directory / "noncanonical.json"
        path.write_text(json.dumps(manifest, indent=2))
        with self.assertRaises(d.Blocked):
            d.load_manifest(path, self.git)
        with self.assertRaises(d.Blocked):
            d.external(self.directory / ".env", self.git)

    def test_unexpected_github_host_is_rejected(self):
        with mock.patch.dict(os.environ, {"GH_HOST": "fictional.invalid"}), self.assertRaises(d.Blocked):
            d.GitHub()

    def test_existing_negative_auth_fixture_exception_is_file_and_value_scoped(self):
        data = ('pass' + 'word="incorrect"').encode()
        d.scan(data, "apps/api/identity/tests/test_authentication.py")
        with self.assertRaises(d.Blocked):
            d.scan(data, "elsewhere.py")
        with self.assertRaises(d.Blocked):
            d.scan(('pass' + 'word="unreviewed"').encode(), "apps/api/identity/tests/test_authentication.py")

    def test_manifest_ignores_user_diff_prefix_and_quoting_preferences(self):
        first, first_hash = self.manifest()
        self.git.run("config", "diff.noprefix", "true")
        self.git.run("config", "diff.mnemonicPrefix", "true")
        self.git.run("config", "core.quotePath", "true")
        second, second_hash = self.manifest(output="different-display-config")
        self.assertEqual((first, first_hash), (second, second_hash))


if __name__ == "__main__":
    unittest.main()
