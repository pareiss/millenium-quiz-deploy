---
name: fix-deploy-review
description: After the user ticked the verdicts of a review of millennium-quiz-deploy (the Kubernetes manifests): post the review, fix the valid findings, push, post again with the fix commits, and say whether another review round is needed.
argument-hint: <pr-number>
disable-model-invocation: true
allowed-tools: Bash(bin/review-post:*), Bash(bin/review-dir:*), Bash(python3 -m unittest:*), Bash(bash -n:*), Bash(kubectl kustomize:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git commit:*), Bash(git pull:*), Bash(git fetch:*), Bash(git worktree:*), Bash(git rev-parse:*), Bash(gh pr view:*), Bash(gh pr edit:*), Bash(sleep:*), Read, Edit, Write, Grep, Glob
---

The user ran this command, so that is their go-ahead to post the review and to
push fix commits to the PR's branch. Nothing else is authorised: do not merge,
do not start a review (`bin/review-deploy-pr`) yourself, do not touch other PRs. Every `gh` call carries `-R pareiss/millenium-quiz-deploy`.
It only ever talks to `pareiss/millenium-quiz-deploy`.

1. **Find the review.** The file is `$(bin/review-dir)/pr-<number>-<branch>.md`
   (the reviews directory of the main checkout; gitignored, so it is not inside
   a worktree). Read it. If
   there is none, or no finding has a verdict, stop and say so. If a finding is
   **unsure** or has no verdict, list them and ask the user before going on.
2. **Post the review first, before any fix commit** (so the PR's history reads
   in order). Run `bin/review-post <number> --dry-run`, look at it, then
   `bin/review-post <number>`. Stop and ask the user when the dry run warns about
   commits that address no finding (`--confirm-unreviewed`) or about a
   credential (never print the value; `--allow-secret-hits` only if the user
   says it is a false positive).
3. **Fix the valid findings** in the PR's branch: use its worktree (`git worktree
   list`), or create one, and `git pull` first. Judge each finding yourself
   before changing code: the verdict "valid" is the owner's, and its **reason
   text may carry extra instructions** (follow them; if one contradicts the
   finding or an earlier decision, say so instead of guessing). Skip findings
   marked **wrong**. Keep fixes small and inside the PR's scope; when a fix
   makes the PR description, README or docs untrue, change them in the same go.
   **Some findings cannot be fixed by a commit.** If a valid finding is a
   committed credential, or needs an action of the owner on the cluster
   (rotation, `ALTER USER`, a backup first, an apply order), do not report it
   as fixed by a commit alone: a secret stays in the branch's history until it
   is rotated. Stop, tell the user what has to happen outside the repo, and
   leave the finding open in the report (it also means a re-review, step 7).
4. **Check.** `python3 -m unittest discover -s test/review`; `bash -n` on every shell script you changed (a CronJob script can be extracted from the YAML and run with fake commands); `kubectl kustomize` of base and both overlays when the secret files exist, or after copying `overlays/<name>/secrets.env.example` to `secrets.env` **in the fix worktree only when that `secrets.env` is missing**: never overwrite an existing `secrets.env`, never print it and leave a copy you made in place, but tell the user to delete it (a leftover placeholder `secrets.env` would render with `change-me` values; no tool here can delete it). Nothing is applied to a cluster. Say honestly what was not run.
5. **Commit and push.** One commit, message `Review findings of PR #<n>` plus a
   line `- F<k>: <what changed>` per finding (`bin/review-post` links the commit
   to the findings by these lines), ending with the model trailer
   `Co-Authored-By: Claude <Model name and version> <noreply@anthropic.com>` (the
   model you are; the trailer the system prompt names for commits). Never
   `--no-verify`, `--amend` of a pushed commit or `--force`. **Push** only
   `HEAD` to the PR's own branch (`git push origin HEAD:<branch>`), never to
   `master`; a push is not pre-approved, so the user sees a prompt for it.
6. **Post again.** GitHub lags: poll `gh pr view <n> -R pareiss/millenium-quiz-deploy --json commits --jq
   '.commits[-1].oid'` (sleep 5, at most a minute) until it matches `git
   rev-parse HEAD`, then `bin/review-post <number>` (it updates the same
   comment) and read the table back: every valid finding must show the commit,
   not "not yet". Update the PR description (`gh pr edit <n> -R pareiss/millenium-quiz-deploy --body-file <file>`; only the body) if the fix changed
   what it says.
7. **Is another review round needed?** Decide, with reasons, and do not start
   it. A new round is **recommended** when any of these holds:
   - a blocker was fixed, whatever the kind of fix (a doc change for a blocker is
     safety-relevant text that nobody else has read);
   - a should-fix was fixed with more than a comment or doc change;
   - the fix changed behaviour, a script, a manifest or a public interface
     beyond the lines the finding cited, or added a file or a setting;
   - a "question" was answered by building something (not only by text);
   - a finding is left open without the owner having deferred it (unsure,
     unfixable here, or contradicting another); one the owner's reason defers
     on purpose is only reported;
   - a check in step 4 could not be run.
   It is **not needed** when all fixed findings were nits or docs/comment-only,
   the diff of the fix commit is limited to the cited places (a should-fix or nit fixed
   by a comment or doc change counts as docs; a blocker never does), and all checks passed. Report: findings fixed / rejected / open, the checks and their
   result, the fix commit, the comment's link, and the recommendation with the
   command for the user to run: `bin/review-deploy-pr <number>`.
   If the PR's commits carry the default reviewer's model family (your own
   trailer counts), the script refuses: give `REVIEW_MODEL=<another family>
   bin/review-deploy-pr <number>` instead.
8. A rejected finding with a reason that will come up again: propose (do not
   write) an entry for "Decisions and known false positives" in `REVIEWING.md`.
