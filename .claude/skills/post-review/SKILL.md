---
name: post-review
description: Post an evaluated review to its pull request (every finding with the user's verdict and reason, and the commits that address it), and offer rejected findings for REVIEWING.md.
argument-hint: <pr-number> [review file]
disable-model-invocation: true
allowed-tools: Bash(bin/review-post:*), Read, Edit
---

Post a review to GitHub only because the user ran this command. This is the
step that makes a review traceable on the PR: why each change was made, and
which findings were rejected and why.

1. Run `bin/review-post $ARGUMENTS --dry-run` and show the user the comment it
   would post: the table of findings with verdicts, reasons and the commits
   that address them. Point out valid findings marked "not yet" (no commit
   names them) and findings that are not evaluated. If the script refuses
   (no review, or no verdicts yet), tell the user why and stop.
2. **Changes the review didn't see.** If the dry run warns that the PR has
   commits after the review that address no finding, show them and ask the
   user whether to post anyway: the review's line numbers and evidence may be
   outdated. Only on yes, add `--confirm-unreviewed` to the next step.
3. Ask for confirmation. On yes, run `bin/review-post $ARGUMENTS` (plus
   `--confirm-unreviewed` if confirmed in step 2) and report the comment's
   link. If the review was posted before, this updates that comment.
4. For each finding marked **wrong** with a reason that will come up again,
   propose a short entry for the "Decisions and known false positives"
   section of `REVIEWING.md`. Add only the entries the user approves; that
   change goes into a normal commit/PR like any other.
