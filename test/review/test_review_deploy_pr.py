"""End-to-end test of bin/review-deploy-pr with a fake `gh` and a fake `claude`.

A temporary git repository with a tiny base/overlay is reviewed. The fake
`claude` records what the reviewer would have been able to read (.review/ and
the checkout), so the test can assert on the prepared material without
calling Claude or GitHub. Needs git and kubectl (or kustomize); skipped
otherwise.

Run: python3 -m unittest discover -s test/review
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COPIED = ["review-dir", "review-post", "review-deploy-pr", "review-flags", "review-tools", "review-cleanup", "review-selfcheck"]

FAKE_GH = r"""#!/usr/bin/env bash
echo "GH_REPO=$GH_REPO $*" >> "$FAKE_LOG/gh.log"
if [[ "$1 $2" == "pr view" && "$*" == *headRefOid* ]]; then
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(git rev-parse feature)" "$(git rev-parse main)" main feature false http://x/pull/7 "Test PR"
elif [[ "$1 $2" == "pr view" ]]; then
  printf '# Test PR\n\nbody\n'
fi
"""

FAKE_CLAUDE = r"""#!/usr/bin/env bash
# the reviewer's view: copy the prepared material and the checkout listing
cp -r .review "$FAKE_LOG/review-material"
find . -path ./.review -prune -o -type f -print | sort > "$FAKE_LOG/checkout.txt"
printf '%s' "$FAKE_RESULT"
"""

KUSTOMIZATION = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
images:
  - name: app
    newName: ghcr.io/o/app
    newTag: "{tag}"
secretGenerator:
  - name: app
    envs:
      - secrets.env
"""


def sh(*cmd, cwd, env=None):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True, env=env)


@unittest.skipUnless(shutil.which("kubectl") or shutil.which("kustomize"), "needs kubectl or kustomize")
class ReviewDeployPrTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        self.log = os.path.join(self.tmp.name, "log")
        self.fakebin = os.path.join(self.tmp.name, "fakebin")
        for d in (self.repo, self.log, self.fakebin):
            os.makedirs(d)
        for name, body in (("gh", FAKE_GH), ("claude", FAKE_CLAUDE)):
            path = os.path.join(self.fakebin, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

        os.makedirs(os.path.join(self.repo, "bin"))
        for name in COPIED:
            shutil.copy(os.path.join(ROOT, "bin", name), os.path.join(self.repo, "bin", name))
        self.write(".claude/agents/pr-reviewer.md", "---\nname: pr-reviewer\nmodel: opus\n---\nBe careful.\n")
        self.write("REVIEWING.md", "# rules\n")
        self.write(".gitignore", "overlays/*/secrets.env\n/reviews/\n/.claude/worktrees/\n")
        self.write("base/kustomization.yaml", "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - deploy.yaml\n")
        self.write("base/deploy.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\nspec:\n  template:\n    spec:\n      containers:\n        - name: app\n          image: app\n")
        self.write("overlays/local/kustomization.yaml", KUSTOMIZATION.format(tag="1.0"))
        self.write("overlays/local/secrets.env.example", "TOKEN=change-me\n")

        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", "git@github.com:me/deploy-repo.git")
        self.git("add", "-A")
        self.commit("base")
        self.git("checkout", "-q", "-b", "feature")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def git(self, *args):
        sh("git", *args, cwd=self.repo)

    def commit(self, message):
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message)

    def review(self, result="## Summary\nFine.\n\n## Findings\nNo findings.\n", model="claude-opus-4-7", cwd=None):
        env = dict(
            os.environ,
            PATH=self.fakebin + os.pathsep + os.environ["PATH"],
            FAKE_LOG=self.log,
            FAKE_RESULT=json.dumps({"result": result, "modelUsage": {model: {}}}),
            REVIEW_SELFCHECKED="1",
            REVIEW_CLEANED="1",
        )
        return subprocess.run(
            ["bin/review-deploy-pr", "7", "--force"], cwd=cwd or self.repo, env=env, capture_output=True, text=True
        )

    def material(self, name):
        with open(os.path.join(self.log, "review-material", name)) as f:
            return f.read()

    def test_prepares_checks_rendered_diff_and_binds_gh_to_origin(self):
        self.write("overlays/local/kustomization.yaml", KUSTOMIZATION.format(tag="2.0"))
        self.git("add", "-A")
        self.commit("bump the tag\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)

        checks = self.material("checks.md")
        self.assertIn("`local`: builds", checks)
        self.assertIn("rendered output CHANGED", checks)
        self.assertIn("No suspicious additions", checks)
        self.assertIn("no CI", self.material("tests.md").replace("\n", " ").replace("This repository has no CI", "no CI"))
        patch = self.material("rendered/diff-local.patch")
        self.assertIn("-      - image: ghcr.io/o/app:1.0", patch)
        self.assertIn("+      - image: ghcr.io/o/app:2.0", patch)

        # the reviewer sees no secrets.env and no generated Secret
        with open(os.path.join(self.log, "checkout.txt")) as f:
            self.assertNotIn("secrets.env", f.read().replace("secrets.env.example", ""))
        self.assertNotIn("kind: Secret", self.material("rendered/head/local.yaml"))

        with open(os.path.join(self.log, "gh.log")) as f:
            self.assertTrue(all(l.startswith("GH_REPO=me/deploy-repo ") for l in f.read().splitlines()))
        with open(os.path.join(self.repo, "reviews", "pr-feature.md")) as f:
            review = f.read()
        self.assertIn("- Reviewer: claude-opus-4-7", review)
        self.assertIn("- Written with: sonnet", review)
        self.assertIn("- CI: none (this repository has no CI)", review)

    def test_secret_tripwire_and_unpinned_image_are_reported(self):
        self.write("base/config.txt", "DB_PASSWORD: sup3rs3cretvalue\n")
        self.write("overlays/local/kustomization.yaml", KUSTOMIZATION.format(tag="latest"))
        self.git("add", "-A")
        self.commit("oops")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        checks = self.material("checks.md")
        self.assertIn("base/config.txt: assigned password/secret/key", checks)
        self.assertNotIn("sup3rs3cretvalue", checks)
        self.assertIn("ghcr.io/o/app:latest: tag latest", checks)

    def test_build_failure_is_reported_not_fatal(self):
        self.write("overlays/local/kustomization.yaml", KUSTOMIZATION.replace("../../base", "../../nope").format(tag="1"))
        self.git("add", "-A")
        self.commit("break it")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("BUILD FAILED", self.material("checks.md"))

    def test_same_model_family_as_author_is_refused(self):
        self.write("base/x.txt", "x\n")
        self.git("add", "-A")
        self.commit("change\n\nCo-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>")
        done = self.review()  # default reviewer model: opus
        self.assertEqual(done.returncode, 1)
        self.assertIn("must be another model", done.stderr)

    def test_answer_from_the_authors_family_is_not_saved(self):
        self.write("base/x.txt", "x\n")
        self.git("add", "-A")
        self.commit("change\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>")
        done = self.review(model="claude-sonnet-5-5")
        self.assertEqual(done.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "reviews", "pr-feature.md")))

    def test_committed_symlinks_are_never_followed_and_the_real_secrets_survive(self):
        # the owner's decrypted secrets, as in the main checkout
        real = os.path.join(self.repo, "overlays", "local", "secrets.env")
        self.write("overlays/local/secrets.env", "TOKEN=REAL-SECRET\n")
        os.symlink(os.path.join(self.repo, "overlays", "local"), os.path.join(self.repo, "overlays", "evil"))
        # relative variant, as a PR would commit it
        os.symlink("../../../../overlays/local", os.path.join(self.repo, "overlays", "evil2"))
        os.makedirs(os.path.join(self.repo, "overlays", "other"))
        self.write("overlays/other/kustomization.yaml", KUSTOMIZATION.format(tag="1.0"))
        os.symlink(real, os.path.join(self.repo, "overlays", "other", "secrets.env"))
        os.symlink(real, os.path.join(self.repo, "overlays", "other", "secrets.env.example"))
        self.git("add", "-A")
        self.git("add", "-f", "overlays/other/secrets.env")
        self.commit("symlinks")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        with open(real) as f:
            self.assertEqual(f.read(), "TOKEN=REAL-SECRET\n")
        checks = self.material("checks.md")
        self.assertIn("overlays/evil", checks)
        self.assertIn("removed from the review checkout", checks)
        self.assertFalse(os.path.exists(os.path.join(self.log, "review-material", "rendered", "head", "evil.yaml")))
        self.assertNotIn("REAL-SECRET", checks)
        with open(os.path.join(self.log, "checkout.txt")) as f:
            self.assertNotIn("evil", f.read())

    def test_kustomizations_reaching_outside_the_checkout_are_refused_not_rendered(self):
        real = os.path.join(self.repo, "overlays", "local")
        self.write("overlays/local/secrets.env", "TOKEN=REAL-SECRET\n")
        outside = os.path.join(self.tmp.name, "outside.env")
        with open(outside, "w") as f:
            f.write("TOKEN=OUTSIDE-SECRET\n")
        evil = {
            "abs": f"resources:\n  - {real}\n",
            "up": "resources:\n  - ../../../repo/overlays/local\n",
            "url": "resources:\n  - https://example.invalid/x.git//base?ref=v1\n",
            "ssh": "resources:\n  - git@github.com:me/private.git//base\n",
            "env": "resources:\n  - ../../base\nsecretGenerator:\n  - name: s\n    envs:\n      - ../../../outside.env\n",
            "viabase": "resources:\n  - ../abs\n",
        }
        for name, body in evil.items():
            self.write(f"overlays/{name}/kustomization.yaml",
                       "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n" + body)
        self.git("add", "-A")
        self.commit("evil overlays")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        checks = self.material("checks.md")
        rendered = os.path.join(self.log, "review-material", "rendered", "head")
        for name in evil:
            self.assertIn(f"`{name}`: NOT RENDERED", checks)
            self.assertFalse(os.path.exists(os.path.join(rendered, name + ".yaml")), name)
        self.assertIn("absolute path", checks)
        self.assertIn("resolves outside the checkout", checks)
        self.assertIn("remote reference", checks)
        self.assertIn("references refused `overlays/abs`", checks)
        self.assertIn("Sandbox:", checks)
        # the legitimate overlay and base still render
        self.assertIn("`local`: builds", checks)
        self.assertIn("`base`: builds", checks)
        for name in os.listdir(rendered):
            with open(os.path.join(rendered, name)) as f:
                self.assertNotIn("SECRET", f.read())
        self.assertNotIn("SECRET", checks)

    def test_review_material_a_pr_committed_is_cleared(self):
        self.write(".review/rendered/head/fake.yaml", "kind: Fake\n")
        self.write(".review/checks.md", "all good\n")
        self.git("add", "-A")
        self.commit("sneak")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.log, "review-material", "rendered", "head", "fake.yaml")))
        checks = self.material("checks.md")
        self.assertNotIn("all good", checks)
        self.assertNotIn("fake.yaml", checks)
        self.assertIn("commits files under .review/", checks)

    def test_the_checkouts_are_outside_the_main_checkout(self):
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".claude", "worktrees")))

    def make_worktree(self):
        wt = os.path.join(self.tmp.name, "wt")
        self.git("worktree", "add", "-q", "--detach", wt, "feature")
        return wt

    def test_reviews_dir_is_the_main_checkouts_also_from_a_worktree(self):
        wt = self.make_worktree()
        expected = os.path.realpath(os.path.join(self.repo, "reviews"))
        for cwd in (self.repo, wt, os.path.join(wt, "base")):
            out = subprocess.run(
                [os.path.join(wt, "bin", "review-dir")], cwd=cwd, check=True, capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(os.path.realpath(out), expected)

    def test_review_run_from_a_worktree_is_written_to_the_main_checkout(self):
        wt = self.make_worktree()
        done = self.review(cwd=wt)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "reviews", "pr-feature.md")))
        self.assertFalse(os.path.exists(os.path.join(wt, "reviews")))


FAKE_GH_POST = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG/gh.log"
if [[ "$1 $2" == "pr view" && "$*" == *headRefName* ]]; then echo feature
elif [[ "$1 $2" == "pr view" ]]; then echo '{"commits": []}'
elif [[ "$1" == api ]]; then echo me
elif [[ "$1 $2" == "pr comment" ]]; then echo http://x/pull/7#issuecomment-1
fi
"""

REVIEW_FOR_POST = """# Review of PR #7: Test

- PR: #7 http://x/pull/7
- Branch: `feature`
- Head: `{head}`

## Summary
Fine.

## Findings

### F1 · nit · a.txt:1 — Title
- **Claim:** {claim}
- **Evidence:** `a`
- **Suggested fix:** None.
- **Confidence:** low
- **Your verdict:** [x] valid  [ ] wrong  [ ] unsure — reason: ok
"""


class ReviewPostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        self.log = os.path.join(self.tmp.name, "log")
        self.fakebin = os.path.join(self.tmp.name, "fakebin")
        for d in (self.repo, self.log, self.fakebin, os.path.join(self.repo, "bin")):
            os.makedirs(d)
        for name in ("review-dir", "review-post", "review-tools"):
            shutil.copy(os.path.join(ROOT, "bin", name), os.path.join(self.repo, "bin", name))
        gh = os.path.join(self.fakebin, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH_POST)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        sh("git", "init", "-q", "-b", "main", cwd=self.repo)
        sh("git", "remote", "add", "origin", "git@github.com:me/deploy-repo.git", cwd=self.repo)
        os.makedirs(os.path.join(self.repo, "reviews"))

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, claim, *flags):
        with open(os.path.join(self.repo, "reviews", "pr-feature.md"), "w") as f:
            f.write(REVIEW_FOR_POST.format(head="a" * 40, claim=claim))
        env = dict(os.environ, PATH=self.fakebin + os.pathsep + os.environ["PATH"], FAKE_LOG=self.log)
        return subprocess.run(["bin/review-post", "7", *flags], cwd=self.repo, env=env, capture_output=True, text=True)

    def test_comment_with_a_credential_is_not_posted(self):
        done = self.post("The config sets DB_PASSWORD: s3cr3tvalue99 in plain text.")
        self.assertEqual(done.returncode, 1)
        self.assertIn("looks like it contains a credential", done.stderr)
        self.assertIn("assigned password/secret/key", done.stderr)
        self.assertNotIn("s3cr3tvalue99", done.stderr + done.stdout)
        with open(os.path.join(self.log, "gh.log")) as f:
            self.assertNotIn("pr comment", f.read())

    def test_allow_secret_hits_posts_anyway_and_clean_comments_post(self):
        done = self.post("The config sets DB_PASSWORD: s3cr3tvalue99 in plain text.", "--allow-secret-hits")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("posted:", done.stdout)
        done = self.post("Nothing sensitive here.")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_dry_run_warns_but_prints(self):
        done = self.post("DB_PASSWORD: s3cr3tvalue99", "--dry-run")
        self.assertEqual(done.returncode, 0)
        self.assertIn("looks like it contains a credential", done.stderr)


if __name__ == "__main__":
    unittest.main()
