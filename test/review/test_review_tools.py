"""Tests for bin/review-tools (review comment builder and the deploy-specific checks).

Run: python3 -m unittest discover -s test/review
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
tools = SourceFileLoader("review_tools", os.path.join(ROOT, "bin", "review-tools")).load_module()

HEAD = "a" * 40

REVIEW = f"""# Review of PR #9: Something

- PR: #9 https://github.com/o/r/pull/9
- Branch: `feature`
- Head: `{HEAD}`
- Reviewer: claude-sonnet-5-5 (bin/review-deploy-pr, isolated process)
- CI: success (https://example/run)
- Date: 2026-10-03T02:15Z

## Summary
Fine.

## Findings

### F1 · should-fix · lib/a.ex:10 — Title with a | pipe
- **Claim:** It breaks.
- **Evidence:** `x | y` at lib/a.ex:10
- **Suggested fix:** Fix it.
- **Confidence:** high
- **Your verdict:** [x] valid  [ ] wrong  [ ] unsure — reason: good catch

### F2 · nit · lib/b.ex:3 — Not a problem
- **Claim:** Maybe.
- **Evidence:** `y`
- **Suggested fix:** None.
- **Confidence:** low
- **Your verdict:** [ ] valid  [X] wrong  [ ] unsure — reason: intended

### F3 · question · lib/c.ex:1 — Unanswered
- **Claim:** ?
- **Evidence:** `z`
- **Suggested fix:** ?
- **Confidence:** low
- **Your verdict:** [ ] valid  [ ] wrong  [ ] unsure — reason:

## Description vs. code
- none
"""


def commit(oid, headline, body="", date="2026-10-03T03:00:00Z"):
    return {"oid": oid * 40, "messageHeadline": headline, "messageBody": body, "committedDate": date}


class ReviewToolsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.review = os.path.join(self.dir.name, "review.md")
        with open(self.review, "w") as f:
            f.write(REVIEW)

    def tearDown(self):
        self.dir.cleanup()

    def commits(self, *commits):
        path = os.path.join(self.dir.name, "commits.json")
        with open(path, "w") as f:
            json.dump({"commits": list(commits)}, f)
        return path

    def run_tool(self, fun, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            fun(*args)
        return out.getvalue()

    def selfcheck(self, events, *outside_paths):
        path = os.path.join(self.dir.name, "stream.json")
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(e) for e in events))
        try:
            self.run_tool(tools.selfcheck, path, "IN-1", "OUT-2", "CTX-3", *outside_paths)
        except SystemExit as e:
            return str(e)
        return None

    @staticmethod
    def stream(denied=True, attempt=True, leak=""):
        init = {"type": "system", "subtype": "init", "tools": ["Read", "Grep", "Glob"]}
        use = lambda i, p: {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "input": {"file_path": p}}]}}
        res = lambda i, text, err: {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": text, "is_error": err}]}}
        events = [init, use("a", "marker.txt"), res("a", "IN-1", False)]
        if attempt:
            events += [use("b", "/p/marker.txt"), res("b", "denied" if denied else "OUT-2", denied)]
        events.append({"type": "result", "result": "done " + leak})
        return events

    def test_selfcheck_passes_when_outside_access_was_attempted_and_denied(self):
        self.assertIsNone(self.selfcheck(self.stream(), "/p/marker.txt"))

    def test_selfcheck_ignores_events_whose_message_is_text(self):
        # The real stream has events with a plain-string message and string blocks.
        events = self.stream()
        events[1:1] = [{"type": "system", "subtype": "notice", "message": "retrying"},
                       {"type": "assistant", "message": {"content": ["plain text block"]}}]
        self.assertIsNone(self.selfcheck(events, "/p/marker.txt"))

    def test_selfcheck_fails_when_outside_access_was_never_attempted(self):
        self.assertIn("never attempted", self.selfcheck(self.stream(attempt=False), "/p/marker.txt"))

    def test_selfcheck_fails_when_outside_read_was_not_denied(self):
        out = self.selfcheck(self.stream(denied=False), "/p/marker.txt")
        self.assertIn("OUTSIDE", out)
        self.assertIn("not denied", out)

    def test_selfcheck_fails_when_a_parent_context_file_was_loaded(self):
        self.assertIn("PARENT", self.selfcheck(self.stream(leak="the code word is CTX-3"), "/p/marker.txt"))

    def test_selfcheck_fails_with_other_tools_or_without_the_inside_read(self):
        events = self.stream()
        events[0]["tools"].append("Bash")
        self.assertIn("available tools", self.selfcheck(events, "/p/marker.txt"))
        self.assertIn("inconclusive", self.selfcheck(self.stream()[:1], "/p/marker.txt"))

    def test_parses_findings_and_verdicts(self):
        header, findings = tools.parse_review(self.review)
        self.assertEqual(header["Head"], f"`{HEAD}`")
        self.assertEqual([f["id"] for f in findings], ["F1", "F2", "F3"])
        self.assertEqual([f["verdict"] for f in findings], ["valid", "wrong", "unmarked"])
        self.assertEqual(findings[0]["reason"], "good catch")
        self.assertEqual(findings[0]["location"], "lib/a.ex:10")

    def test_only_fix_lines_count(self):
        msgs = {
            "- F1: fixed": {"F1"},
            "- F1, F5: both": {"F1", "F5"},
            "F2: at the start": {"F2"},
            "F2 is a false positive": set(),
            "From the review (F1, marked valid): x": set(),
            "Formula F1 race": set(),
        }
        for message, expected in msgs.items():
            self.assertEqual(tools.named_findings({"messageHeadline": "x", "messageBody": message}), expected, message)

    def test_after_review_by_order_not_date(self):
        # rewritten dates: a commit before the reviewed one has a later date
        before = commit("b", "older", "- F1: fixed long ago", date="2026-10-03T09:00:00Z")
        reviewed = {"oid": HEAD, "messageHeadline": "reviewed", "messageBody": "", "committedDate": "2026-10-03T09:00:00Z"}
        fix = commit("c", "fix", "- F1: done")
        path = self.commits(before, reviewed, fix)
        after = tools.commits_after_review(path, HEAD, "2026-10-03T02:15:00Z")
        self.assertEqual([c["oid"][0] for c in after], ["c"])
        self.assertEqual(tools.fixing_commits(after), {"F1": ["ccccccc"]})

    def test_falls_back_to_date_when_head_was_rebased_away(self):
        old = commit("b", "before review", "- F1: x", date="2026-10-03T01:00:00Z")
        new = commit("c", "after review", "- F1: y", date="2026-10-03T03:00:00Z")
        after = tools.commits_after_review(self.commits(old, new), HEAD, "2026-10-03T02:15:00Z")
        self.assertEqual([c["oid"][0] for c in after], ["c"])

    def test_post_body(self):
        path = self.commits(
            {"oid": HEAD, "messageHeadline": "reviewed", "messageBody": "", "committedDate": "2026-10-03T02:00:00Z"},
            commit("c", "Address review", "- F1: fixed\nF2 is a false positive"),
        )
        body = self.run_tool(tools.post_body, self.review, path, "someone")
        self.assertIn("@someone", body)
        self.assertIn("| F1 | should-fix | Title with a \\| pipe | ✅ valid | good catch | ccccccc |", body)
        self.assertIn("| F2 | nit | Not a problem | ❌ wrong | intended | – |", body)
        self.assertIn("| F3 | question | Unanswered | – not evaluated | – | – |", body)
        self.assertIn("<details>", body)

    def test_since_review_lists_commits_naming_no_finding(self):
        path = self.commits(
            {"oid": HEAD, "messageHeadline": "reviewed", "messageBody": "", "committedDate": "2026-10-03T02:00:00Z"},
            commit("c", "Address review", "- F1: fixed"),
            commit("d", "Unrelated change"),
        )
        self.assertEqual(self.run_tool(tools.since_review, self.review, path), "ddddddd Unrelated change\n")

    def test_mark_posted_adds_then_replaces(self):
        tools.mark_posted(self.review, "T1", "https://x/1#issuecomment-1")
        tools.mark_posted(self.review, "T2", "https://x/1#issuecomment-1")
        with open(self.review) as f:
            text = f.read()
        self.assertEqual(text.count("- Posted: "), 1)
        self.assertIn("- Date: 2026-10-03T02:15Z\n- Posted: T2 https://x/1#issuecomment-1\n", text)

    def test_repo_from_origin_urls(self):
        for url in (
            "git@github.com:pareiss/millenium-quiz-deploy.git",
            "https://github.com/pareiss/millenium-quiz-deploy",
            "https://github.com/pareiss/millenium-quiz-deploy.git/",
        ):
            self.assertEqual(self.run_tool(tools.repo, url), "pareiss/millenium-quiz-deploy\n", url)
        with self.assertRaises(SystemExit):
            tools.repo("git@gitlab.com:a/b.git")

    def test_family(self):
        self.assertEqual(tools.family("claude-sonnet-5-5"), "sonnet")
        self.assertEqual(tools.family("Claude-Opus-4-7[1m]"), "opus")

    def test_annotate_diff_numbers_head_lines(self):
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,2 +10,2 @@\n ctx\n-old\n+new\n"
        import sys
        old, sys.stdin = sys.stdin, io.StringIO(diff)
        try:
            out = self.run_tool(tools.annotate_diff)
        finally:
            sys.stdin = old
        self.assertIn("    10 | ctx", out)
        self.assertIn("       |-old", out)
        self.assertIn("    11 |+new", out)

    def scan(self, diff):
        return self.run_tool(tools.scan_secrets, diff).splitlines()

    def test_scan_secrets_flags_credentials_without_printing_them(self):
        diff = (
            "+++ b/base/app/configmap.yaml\n"
            "+  DB_PASSWORD: s3cr3tvalue99\n"
            "+  url: postgres://admin:hunter2hunter2@db/x\n"
            "+++ b/overlays/production/secrets.env\n"
            "+A=b\n"
            "+++ b/notes.md\n"
            "+key AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ\n"
        )
        out = self.scan(diff)
        self.assertIn("base/app/configmap.yaml: assigned password/secret/key", out)
        self.assertIn("base/app/configmap.yaml: registry/basic-auth credentials in a URL", out)
        self.assertIn("overlays/production/secrets.env: file name is one that holds plaintext credentials", out)
        self.assertIn("notes.md: age private key", out)
        self.assertNotIn("s3cr3tvalue99", "\n".join(out))
        self.assertNotIn("hunter2", "\n".join(out))

    def test_scan_secrets_accepts_placeholders_references_and_sops(self):
        diff = (
            "+++ b/overlays/local/secrets.env.example\n"
            "+POSTGRES_PASSWORD=change-me-hex\n"
            "+++ b/base/postgres/statefulset.yaml\n"
            "+            - name: POSTGRES_PASSWORD\n"
            "+  secretName: millennium-quiz-tls\n"
            "+++ b/overlays/local/secrets.enc.env\n"
            "+POSTGRES_PASSWORD=ENC[AES256_GCM,data:abc,iv:x,tag:y,type:str]\n"
            "+sops_version=3.9.0\n"
        )
        self.assertEqual(self.scan(diff), [])

    def test_scan_secrets_flags_unencrypted_enc_env_line(self):
        out = self.scan("+++ b/overlays/x/secrets.enc.env\n+POSTGRES_PASSWORD=plain\n")
        self.assertEqual(out, ["overlays/x/secrets.enc.env: line not encrypted by SOPS (no ENC[...])"])

    def test_scan_secrets_flags_commented_out_credentials(self):
        out = self.scan(
            "+++ b/overlays/x/postgres.txt\n"
            "+# POSTGRES_PASSWORD=hunter2hunter2\n"
            "+++ b/a.yaml\n"
            "+  # token: " + "A" * 50 + "\n"
            "+++ b/b.yaml\n"
            "+  ## api_key: abcdefgh1234\n"
        )
        self.assertEqual(out, [
            "overlays/x/postgres.txt: assigned password/secret/key",
            "a.yaml: base64 .dockerconfigjson / kubeconfig token",
            "b.yaml: assigned password/secret/key",
        ])
        self.assertEqual(self.scan("+++ b/a.yaml\n+# a plain comment about passwords\n+# DB_PASSWORD: change-me-now\n"), [])

    def test_scan_secrets_comments_are_skipped_only_in_enc_env(self):
        self.assertEqual(self.scan("+++ b/overlays/x/secrets.enc.env\n+# POSTGRES_PASSWORD=plain\n"), [])

    def test_scan_secrets_placeholder_word_elsewhere_on_the_line_does_not_hide_a_secret(self):
        out = self.scan(
            "+++ b/a.yaml\n"
            "+  url: postgres://admin:hunter2hunter2@db/x  # example\n"
            "+  DB_PASSWORD: s3cr3tvalue99  # see the example in docs\n"
        )
        self.assertEqual(out, [
            "a.yaml: registry/basic-auth credentials in a URL",
            "a.yaml: assigned password/secret/key",
        ])

    def test_strip_secrets_drops_secret_documents_only(self):
        rendered = "kind: ConfigMap\nmetadata:\n  name: a\n---\nkind: Secret\ndata:\n  X: eQ==\n---\nkind: Service\n"
        out = self.run_tool(tools.strip_secrets, rendered)
        self.assertIn("kind: ConfigMap", out)
        self.assertIn("kind: Service", out)
        self.assertNotIn("kind: Secret", out)
        self.assertNotIn("eQ==", out)

    def test_scan_secrets_flags_api_key_but_not_key_names(self):
        self.assertEqual(self.scan("+++ b/a.yaml\n+  api_key: abcdefgh1234\n"), ["a.yaml: assigned password/secret/key"])
        self.assertEqual(self.scan("+++ b/a.yaml\n+  secretKeyRef:\n+  key: POSTGRES_PASSWORD\n"), [])

    def test_image_lint(self):
        rendered = (
            "        image: postgres:17\n"
            "      - image: ghcr.io/o/app:0.1.0\n"
            "        image: busybox\n"
            "        image: localhost:5000/app\n"
            "        image: nginx:latest\n"
            "        image: repo/app@sha256:" + "a" * 64 + "\n"
        )
        out = self.run_tool(tools.image_lint, rendered).splitlines()
        self.assertEqual(out, ["busybox: no tag", "localhost:5000/app: no tag", "nginx:latest: tag latest"])


class CheckKustomizationsTest(unittest.TestCase):
    def check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            root = os.path.join(tmp, "co")
            for rel, text in files.items():
                path = os.path.join(root, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(text)
            buf = io.StringIO()
            with redirect_stdout(buf):
                tools.check_kustomizations(root)
            return [l.split("\t") for l in buf.getvalue().splitlines()]

    def k(self, body):
        return "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n" + body

    def test_legit_layout_passes(self):
        self.assertEqual(self.check({
            "base/kustomization.yaml": self.k("resources:\n  - a/b.yaml\n"),
            "overlays/x/kustomization.yaml": self.k(
                "resources:\n  - ../../base\nsecretGenerator:\n  - name: s\n    envs:\n      - secrets.env\n"
                "patches:\n  - target: {kind: ConfigMap}\n    patch: |-\n      - op: replace\n        path: /data/X\n        value: y\n"
                "  - path: p.yaml\n"),
        }), [])

    def test_each_kind_of_escape_is_refused(self):
        cases = {
            "resources:\n  - /etc\n": "absolute",
            "resources:\n  - ../../x\n": "outside",
            "components:\n  - ../../../c\n": "outside",
            "bases:\n  - ../..\n": "outside",
            "resources:\n  - https://h/x\n": "remote",
            "resources:\n  - git@github.com:o/r.git\n": "remote",
            "resources:\n  - github.com/o/r//base?ref=v1\n": "remote",
            "resources:\n  - gitlab.example.org/o/r\n": "remote",
            "patches:\n  - path: /etc/passwd\n": "absolute",
            "patchesStrategicMerge:\n  - ../../p.yaml\n": "outside",
            "patchesJson6902:\n  - path: ../../p.json\n    target: {kind: X}\n": "outside",
            "secretGenerator:\n  - name: s\n    envs:\n      - ../../s.env\n": "outside",
            "configMapGenerator:\n  - name: s\n    files:\n      - k=/etc/hosts\n": "absolute",
            "configMapGenerator:\n  - name: s\n    env: ../../e\n": "outside",
            "replacements:\n  - path: ../../r.yaml\n": "outside",
            "helmCharts:\n  - name: c\n": "helm",
            "resources: [": "parsed",
        }
        for body, word in cases.items():
            found = self.check({"x/kustomization.yaml": self.k(body)})
            self.assertTrue(found and found[0][0] == "x" and word in found[0][1], (body, found))

    def test_dir_dotdot_inside_the_checkout_is_fine_and_taint_propagates(self):
        found = self.check({
            "a/kustomization.yaml": self.k("resources:\n  - ../b/../c\n"),
            "c/kustomization.yaml": self.k("resources:\n  - /abs\n"),
            "d/kustomization.yml": self.k("resources:\n  - ../a\n"),
        })
        dirs = sorted(d for d, _ in found)
        self.assertEqual(dirs, ["a", "c", "d"])
        self.assertTrue(any("references refused `c`" in r for d, r in found if d == "a"))
        self.assertTrue(any("references refused `a`" in r for d, r in found if d == "d"))


if __name__ == "__main__":
    unittest.main()
