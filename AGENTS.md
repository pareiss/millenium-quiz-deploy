# Agent rules for millennium-quiz-deploy

For every Claude Code agent (or human) working in this repo. The sister repos
`pareiss/millennium-quiz` and `pareiss/millennium-quiz-authoring` have the same
shape of rules (`AGENTS.md`, `REVIEWING.md`); keep all three in step so work
looks the same in each. Only the review entry point differs on purpose:
here it is `bin/review-deploy-pr`, in the quiz repo `bin/review-pr`.

Read first: `README.md` (layout, first deployment, secrets), then
`REVIEWING.md` (the invariants a change is judged against).

## What this repo is
- Kubernetes manifests (kustomize: `base/`, `overlays/<name>/`) and cluster-wide
  setup (`cluster/`) for running Millennium Quiz on the owner's k3s cluster,
  with SOPS + age encrypted secrets (`bin/secrets`).
- The app's code is in `pareiss/millennium-quiz`; nothing from it is copied here.
- The cluster is real and holds the only copy of the data. This repo's files
  are proposals until the owner applies them by hand.

## Hard rules
- **Never apply anything to a cluster.** No `kubectl apply`, `delete`, `patch`,
  `replace`, `rollout`, `scale`, `exec`, `drain`, `helm install` or the like,
  no `gigalixir` command that changes something, and no deploying, unless the
  user asks for that exact action in this conversation. Read-only, offline
  rendering is fine: `kubectl kustomize overlays/<name>` or `kustomize build`
  needs no cluster. Do not point `kubectl` at a kubeconfig on your own.
- **No secrets in git, in output or in PRs.** No real passwords, tokens, keys,
  `SECRET_KEY_BASE`, kubeconfigs or age private keys in a commit, a PR text, a
  review or your reports. Never print the content of `secrets.env`, `secrets.enc.env`
  after decrypting, `~/.config/sops/age/keys.txt` or `gigalixir config`. Do not
  run `bin/secrets decrypt/edit` unless the user asks. Placeholders go in
  `*.example` files. Enable the guard once per clone:
  `git config core.hooksPath .githooks`.
- **Base stays environment-independent**; environment values live in overlays
  (see `REVIEWING.md`).
- **Pin images** (tag or digest, never `latest`) and keep resource limits.
- **Back up before anything that touches Postgres or the app version**, and
  write the rollback down (`REVIEWING.md`, "Database and rollout safety").
- Do not edit a file you have not read. Do not add tools or dependencies
  without a reason written in the PR.
- Report outcomes faithfully: if a render, a check or a step was not run or
  failed, say so. Nothing here can be tested by running it against a cluster;
  say "rendered, not applied", never "works".

## Working rules
- Small, single-purpose pull requests, one branch each, branched from
  `master`. Branch names are short and describe the change (`review-tooling`,
  `tls-production`). Work on a branch in a worktree under `.claude/worktrees/`.
- Before you commit, render every overlay you touched (or whose base you
  touched): `kubectl kustomize overlays/local` (copy `secrets.env.example` to
  the gitignored `secrets.env` first if you have no decrypted one), and run
  `python3 -m unittest discover -s test/review` if you touched `bin/review-*`.
- Commit messages: an imperative first line, then the why. End with the
  `Co-Authored-By` trailer for the model that wrote the change:
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`
- Keep the README true: a changed deploy step, new Secret key or cluster-wide
  object is documented in the same PR.

## Pull request process
1. Commit locally on the branch. **Push and open the PR only when the user
   says so.** An agent that finished its work hands back a report; it does not
   push on its own.
2. The PR description says what changed, how it was verified (the commands and
   what they showed), what was *not* verified (anything needing a cluster), the
   deploy steps for the owner (backup, apply order, rollback) and open
   questions. End it with:
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
3. **Reviews are started by the user, never by an agent.** Do not run
   `bin/review-deploy-pr`, `bin/review-deploy-open-prs` or `/review-deploy-pr`
   on your own. The review is done by a different model family than the one
   that wrote the change (the `Co-Authored-By` trailers decide), in an isolated
   process that sees only the diff, the rendered manifests and the written
   docs. Every finding is a claim the owner judges; the review file is
   `reviews/pr-<number>-<branch>.md` in the main checkout (gitignored; `bin/review-dir`
   prints the path, also when you work in a worktree).
4. The owner ticks "valid", "wrong" or "unsure" in the review file. The user's
   command `/fix-deploy-review <PR>` does the whole round: post the review, fix
   the valid findings, push, post again with the fix commits and say whether
   another review is needed (it never starts one). Without that command, when
   the user says the verdicts are marked, first remind them to run
   `/post-review <PR>` (`bin/review-post`), so the PR shows every finding with
   its verdict and reason; then fix the accepted findings in new commits on the
   same branch (message `Review findings of PR #N`, with a line `- F2: …` per
   finding so `bin/review-post` can link the commit to it) and report which
   were fixed and which rejected, with the reason.
5. After a push, wait until GitHub lists the pushed commit (`gh pr view N
   --json commits`) before `bin/review-post`, then read the comment back.
6. The owner merges. After a merge, check that stacked PRs still target
   `master` (say "change base to master" if not), then delete the merged
   branch and its worktree.
7. A rejected finding that will come up again becomes an entry under
   "Decisions and known false positives" in `REVIEWING.md`.

## Review tooling in short
- `bin/review-deploy-pr <n>`: one PR; `bin/review-deploy-open-prs`: all open
  PRs; `bin/review-post <n>`; `bin/review-cleanup`; `bin/review-selfcheck`.
  `gh` is bound to this repository (the origin remote) by every script.
- The reviewer has only Read, Grep and Glob. The script prepares `.review/`
  for it, including offline manifest checks; see the README section
  "Reviewing pull requests".
- Changing the reviewer's instructions (`.claude/agents/pr-reviewer.md`) or the
  isolation flags (`bin/review-flags`) is a normal PR; run
  `python3 -m unittest discover -s test/review` and `bin/review-selfcheck --force`
  (the latter calls Claude, so only when the user asks).

## Decisions and known false positives
See the section of the same name in `REVIEWING.md`.
