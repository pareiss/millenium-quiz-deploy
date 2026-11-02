---
name: review-deploy-open-prs
description: Review every open pull request, each in its own isolated reviewer process, and save the reviews to reviews/ (of the main checkout, see bin/review-dir) for the user to evaluate.
argument-hint: [--force] [--jobs N]
disable-model-invocation: true
allowed-tools: Bash(bin/review-deploy-open-prs:*), Read
---

Run `bin/review-deploy-open-prs $ARGUMENTS` from the repository root and wait for it
to finish (several reviews run in parallel; use a timeout of at least 30
minutes, or run it in the background and wait for the notification).

The reviews must stay independent of this session:
- Do not pass anything else to the reviewers.
- Do not read the PRs yourself before or during the run.

The script first archives the reviews of PRs that are already merged or
closed (`bin/review-cleanup`); mention which ones it moved.

When it is done, show the summary table the script printed. For each review
file, quote the first sentence of its "## Summary" section. For a failed PR,
show the last lines of its log, `logs/pr-<branch>.log` under the directory `bin/review-dir` prints (the main checkout's `reviews/`, also from a worktree) (the branch
name with `/` and other special characters replaced by `-`).

Do not judge, fix or post the findings. The user evaluates them in the files
and can then run `/post-review <number>`.
