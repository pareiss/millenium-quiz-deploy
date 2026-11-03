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
            with open(os.path.join(wt, "reviews", "pr-old.md"), "w") as f:
                f.write("old")
            r = run_helper(wt)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, os.path.join(main, "reviews") + "\n")
            self.assertIn("pr-old.md", r.stderr)
            self.assertNotIn("cp -rn", r.stderr)
            # nothing was moved or deleted
            self.assertTrue(os.path.exists(os.path.join(wt, "reviews", "pr-old.md")))
            # a caller that already noticed can silence it
            self.assertEqual(run_helper(wt, REVIEW_STRAY_NOTICED="1").stderr, "")

    def test_suggested_commands_never_lose_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            main, wt = make_repo(tmp)
            mr, sr = os.path.join(main, "reviews"), os.path.join(wt, "reviews")

            def put(base, rel, text):
                path = os.path.join(base, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(text)

            put(mr, "pr-clash.md", "main version")
            put(mr, "archive/pr-x-1.md", "main archive")
            put(sr, "pr-clash.md", "stray version")
            put(sr, "pr-new one's.md", "new")
            put(sr, "archive/pr-x-1.md", "stray archive")
            put(sr, "archive/pr-y-2.md", "y")
            put(sr, "logs/odd $name `x`.log", "log")
            before = os.stat(os.path.join(mr, "pr-clash.md")).st_mtime
            r = run_helper(wt)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = r.stderr.splitlines()
            self.assertIn("  pr-clash.md", lines)  # listed as clashing
            self.assertIn("  archive/pr-x-1.md", lines)
            cmds = [l.strip() for l in lines if l.startswith("  mkdir ") or l.startswith("  find ")]
            self.assertEqual(len(cmds), 2, r.stderr)
            for cmd in cmds:
                done = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)

            def read(base, rel):
                with open(os.path.join(base, rel)) as f:
                    return f.read()

            # the clashing files were not touched in either place
            self.assertEqual(read(mr, "pr-clash.md"), "main version")
            self.assertEqual(read(sr, "pr-clash.md"), "stray version")
            self.assertEqual(read(mr, "archive/pr-x-1.md"), "main archive")
            self.assertEqual(read(sr, "archive/pr-x-1.md"), "stray archive")
            self.assertEqual(os.stat(os.path.join(mr, "pr-clash.md")).st_mtime, before)
            # the others arrived (odd names included) and left the stray directory
            self.assertEqual(read(mr, "pr-new one's.md"), "new")
            self.assertEqual(read(mr, "archive/pr-y-2.md"), "y")
            self.assertEqual(read(mr, "logs/odd $name `x`.log"), "log")
            self.assertFalse(os.path.exists(os.path.join(sr, "pr-new one's.md")))
            self.assertFalse(os.path.exists(os.path.join(sr, "logs")))
            # still there because it holds a clashing file
            self.assertTrue(os.path.isdir(sr))

    def test_emptied_stray_directory_is_removed_and_a_file_in_it_never(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            main, wt = make_repo(tmp)
            os.makedirs(os.path.join(main, "reviews"))
            os.makedirs(os.path.join(wt, "reviews", "sub"))
            with open(os.path.join(wt, "reviews", "sub", "pr-a.md"), "w") as f:
                f.write("a")
            r = run_helper(wt)
            for cmd in [l.strip() for l in r.stderr.splitlines() if l.startswith(("  mkdir ", "  find "))]:
                subprocess.run(["bash", "-c", cmd], check=True, capture_output=True)
            self.assertFalse(os.path.exists(os.path.join(wt, "reviews")))
            with open(os.path.join(main, "reviews", "sub", "pr-a.md")) as f:
                self.assertEqual(f.read(), "a")


if __name__ == "__main__":
    unittest.main()
