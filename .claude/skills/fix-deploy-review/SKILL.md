---
name: fix-deploy-review
description: After the user ticked the verdicts of a review of millennium-quiz-deploy (the Kubernetes manifests): post the review, fix the valid findings, push, post again with the fix commits, and say whether another review round is needed.
argument-hint: <pr-number>
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

The user ran this command, so that is their go-ahead to post the review and to
push fix commits to the PR's branch. Nothing else is authorised: do not merge,
do not start a review (`bin/review-deploy-pr`) yourself, do not touch other PRs.
It only ever talks to `pareiss/millenium-quiz-deploy`.

1. **Find the review.** The file is `reviews/pr-<number>-<branch>.md` in the main
   checkout (the first entry of `git worktree list`; gitignored, so it is not in a
   worktree). Read it. If
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
4. **Check.** `python3 -m unittest discover -s test/review`; `bash -n` on every shell script you changed (a CronJob script can be extracted from the YAML and run with fake commands); `kubectl kustomize` of base and both overlays when the secret files exist or the `*.example` copies can be used. Nothing is applied to a cluster. Say honestly what was not run.
5. **Commit and push.** One commit, message `Review findings of PR #<n>` plus a
   line `- F<k>: <what changed>` per finding (`bin/review-post` links the commit
   to the findings by these lines), ending with the model trailer
   `Co-Authored-By: <the model you are>` as in the commits of this PR. Push.
6. **Post again.** GitHub lags: poll `gh pr view <n> --json commits --jq
   '.commits[-1].oid'` (sleep 5, at most a minute) until it matches `git
   rev-parse HEAD`, then `bin/review-post <number>` (it updates the same
   comment) and read the table back: every valid finding must show the commit,
   not "not yet". Update the PR description (`gh pr edit`) if the fix changed
   what it says.
7. **Is another review round needed?** Decide, with reasons, and do not start
   it. A new round is **recommended** when any of these holds:
   - a blocker or should-fix was fixed with more than a comment or doc change;
   - the fix changed behaviour, a script, a manifest or a public interface
     beyond the lines the finding cited, or added a file or a setting;
   - a "question" was answered by building something (not only by text);
   - a finding is left open without the owner having deferred it (unsure,
     unfixable here, or contradicting another); one the owner's reason defers
     on purpose is only reported;
   - a check in step 4 could not be run.
   It is **not needed** when all fixed findings were nits or docs/comment-only,
   the diff of the fix commit is limited to the cited places (a should-fix fixed
   by a comment or doc change counts as docs), and all checks passed. Report: findings fixed / rejected / open, the checks and their
   result, the fix commit, the comment's link, and the recommendation with the
   command for the user to run: `bin/review-deploy-pr <number>`.
8. A rejected finding with a reason that will come up again: propose (do not
   write) an entry for "Decisions and known false positives" in `REVIEWING.md`.
