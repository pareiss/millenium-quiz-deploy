---
name: review-deploy-pr
description: Review one pull request in a separate, isolated reviewer process and save the review to reviews/ (of the main checkout, see bin/review-dir) for the user to evaluate.
argument-hint: <pr-number> [--force]
disable-model-invocation: true
allowed-tools: Bash(bin/review-deploy-pr:*), Read
---

Run `bin/review-deploy-pr $ARGUMENTS` from the repository root and wait for it to
finish (a review takes a few minutes; use a timeout of at least 15 minutes).

The review must stay independent of this session:
- Do not pass anything else to the reviewer, and do not run the reviewer any
  other way than through the script.
- Do not read the PR, its diff or its description yourself before or during the
  run.

When it is done, report in a few lines:
- the file it wrote, and the number of findings per severity (the script prints
  both), or that it was skipped because this commit was already reviewed
  (suggest `--force` to review again), or the error if it failed;
- the review's own summary, quoted from the file's "## Summary" section.

Do not judge, fix or post the findings. The user evaluates them by ticking
"valid", "wrong" or "unsure" in the file, and can then run `/post-review`.
