"""bin/review-dir: reviews/ is always the main checkout's, also from a worktree."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPER = os.path.join(ROOT, "bin", "review-dir")


def git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def run_helper(cwd, **env):
    return subprocess.run([HELPER], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **env})


def make_repo(tmp, *init_args):
    main = os.path.join(tmp, "main")
    wt = os.path.join(tmp, "wt")
    os.mkdir(main)
    git(main, "init", "-q", *init_args)
    git(main, "commit", "-q", "--allow-empty", "-m", "init")
    git(main, "worktree", "add", "-q", "-b", "other", wt)
    return main, wt


class ReviewDirTest(unittest.TestCase):
    def test_worktree_uses_main_checkouts_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            main, wt = make_repo(tmp)
            sub = os.path.join(wt, "sub")
            os.mkdir(sub)
            for cwd in (main, wt, sub):
                r = run_helper(cwd)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout, os.path.join(main, "reviews") + "\n")

    def test_separate_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            main, wt = make_repo(tmp, "--separate-git-dir", os.path.join(tmp, "elsewhere"))
            self.assertEqual(run_helper(main).stdout, os.path.join(main, "reviews") + "\n")
            r = run_helper(wt)
            self.assertEqual(r.returncode, 1)
            self.assertIn("cannot find the main checkout", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_bare_repo_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare = os.path.join(tmp, "bare.git")
            git(tmp, "init", "-q", "--bare", bare)
            r = run_helper(bare)
            self.assertEqual(r.returncode, 1)
            self.assertIn("bare repository; there is no main checkout", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_not_a_repo_fails_without_slash_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_helper(tmp, GIT_CEILING_DIRECTORIES=os.path.dirname(tmp))
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout, "")
            self.assertNotEqual(r.stderr, "")

    def test_empty_or_relative_result_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "fakebin")
            os.mkdir(fake)
            for output in ("", "relative/.git"):
                with open(os.path.join(fake, "git"), "w") as f:
                    f.write(f"#!/bin/sh\ncase \"$*\" in *is-bare*) echo false;; *) echo '{output}';; esac\n")
                os.chmod(os.path.join(fake, "git"), 0o755)
                r = run_helper(tmp, PATH=fake + ":" + os.environ["PATH"])
                self.assertEqual(r.returncode, 1, output)
                self.assertEqual(r.stdout, "", output)
                self.assertIn("cannot determine the main checkout", r.stderr)

    def test_stray_worktree_reviews_get_a_notice_on_stderr_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            main, wt = make_repo(tmp)
            # the main checkout's own reviews/ is never "stray"
            os.mkdir(os.path.join(main, "reviews"))
            open(os.path.join(main, "reviews", "pr-a.md"), "w").close()
            self.assertEqual(run_helper(main).stderr, "")
            # an empty reviews/ in the worktree is not worth a notice
            os.mkdir(os.path.join(wt, "reviews"))
            self.assertEqual(run_helper(wt).stderr, "")
            open(os.path.join(wt, "reviews", "pr-old.md"), "w").close()
            r = run_helper(wt)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, os.path.join(main, "reviews") + "\n")
            self.assertIn(os.path.join(wt, "reviews", "pr-old.md"), r.stderr)
            self.assertIn(
                f"mkdir -p '{main}/reviews' && cp -rn '{wt}/reviews/.' '{main}/reviews/' && rm -r '{wt}/reviews'",
                r.stderr)
            # nothing was moved or deleted
            self.assertTrue(os.path.exists(os.path.join(wt, "reviews", "pr-old.md")))
            # a caller that already noticed can silence it
            self.assertEqual(run_helper(wt, REVIEW_STRAY_NOTICED="1").stderr, "")


if __name__ == "__main__":
    unittest.main()
