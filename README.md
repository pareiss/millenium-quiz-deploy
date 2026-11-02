# millennium-quiz-deploy

Kubernetes manifests (kustomize) for [Millennium Quiz](../millennium-quiz).
The application code lives in its own repository; this repo only decides
*how* and *where* it runs.

```
base/                       environment-independent resources
  namespace.yaml            namespace millennium-quiz
  postgres/                 StatefulSet (postgres:17, 2Gi PVC) + headless Service
  app/deployment.yaml       Phoenix release, 1 replica
                            initContainer `migrate`: migrations + first admin
                            probes: /healthz/live (startup, liveness), /healthz/ready (DB)
  app/service.yaml          ClusterIP :80 -> 4000
  app/service-headless.yaml for future BEAM clustering (DNS_CLUSTER_QUERY)
  app/configmap.yaml        non-secret runtime config
  app/ingress.yaml          Traefik (k3s default)
overlays/local/             local k3s: http://millennium-quiz.localhost
  kustomization.yaml        image tag, host, http URL, secret generation
  secrets.env.example       copy to secrets.env (gitignored)
```

## First deployment on a fresh k3s

```sh
# 1. kubectl access as your user (k3s writes a root-only kubeconfig)
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown "$USER" ~/.kube/config

# 2. build the app image and import it into k3s' containerd (no registry needed)
cd ../millennium-quiz
podman build -t millennium-quiz:0.1.0 .
podman save millennium-quiz:0.1.0 | sudo k3s ctr images import -
sudo k3s ctr images ls | grep millennium-quiz   # should show localhost/millennium-quiz:0.1.0

# 3. secrets (see "Secrets" below; needs the age key)
cd ../millennium-quiz-deploy
git config core.hooksPath .githooks   # once per clone: blocks plaintext secrets
bin/secrets decrypt local             # secrets.enc.env -> gitignored secrets.env

# 4. deploy
kubectl apply -k overlays/local
kubectl -n millennium-quiz get pods -w
```

Open http://millennium-quiz.localhost. Browsers resolve `*.localhost` to
127.0.0.1, and Traefik listens on port 80 of the node. The admin from
`ADMIN_USERNAME` / `ADMIN_PASSWORD` can log in at `/admin/login`.

Optionally, load the sample category:

```sh
kubectl -n millennium-quiz exec deploy/millennium-quiz -- \
  bin/millennium_quiz eval 'MillenniumQuiz.Release.seed()'
```

## Day-to-day

```sh
kubectl kustomize overlays/local          # render without applying (works offline)
kubectl -n millennium-quiz logs deploy/millennium-quiz -c migrate   # migration output
kubectl -n millennium-quiz logs deploy/millennium-quiz -f

# new app version: build + import a new tag, bump newTag in overlays/local, then
kubectl apply -k overlays/local

# another admin
kubectl -n millennium-quiz exec deploy/millennium-quiz -- \
  bin/millennium_quiz eval 'MillenniumQuiz.Release.create_admin("name", "a long password")'

# psql
kubectl -n millennium-quiz exec -it postgres-0 -- psql -U millennium_quiz millennium_quiz
```

## Secrets

Secrets are committed only in encrypted form, with [SOPS](https://getsops.io)
and [age](https://age-encryption.org):

```
overlays/<overlay>/secrets.enc.env   committed; values encrypted, keys readable
overlays/<overlay>/secrets.env       gitignored plaintext that kustomize reads
.sops.yaml                           which age public keys can decrypt
```

```sh
bin/secrets edit local       # edit encrypted in $EDITOR, then refresh secrets.env
bin/secrets decrypt local    # after a pull: secrets.enc.env -> secrets.env
bin/secrets encrypt local    # after editing secrets.env directly
kubectl apply -k overlays/local
```

The private age key lives in `~/.config/sops/age/keys.txt` and never goes
into git. **Back it up** (e.g. in a password manager): without it the
encrypted files can't be decrypted. To give a teammate or CI access, add
their age public key to `.sops.yaml` and run `bin/secrets updatekeys local`.

For a new overlay, copy `secrets.env.example` to `overlays/<name>/secrets.env`,
fill it in and run `bin/secrets encrypt <name>`. The pre-commit hook
refuses plaintext `secrets.env` files and `*.enc.env` files that are not
SOPS-encrypted.

Changing `secrets.env` creates a new secret with a different hash suffix,
and the pods restart with the new values. One exception: Postgres only reads
`POSTGRES_PASSWORD` when it initializes an *empty* volume. On an existing
database, change the password with `ALTER USER` first.

## Notes and next steps

- **Replicas:** the app runs with 1 replica and `Recreate`, because every running game is a process in a node-local Registry. Scaling out needs clustering (the headless service and `DNS_CLUSTER_QUERY` are already in place) plus `:global` or Horde registration in the app.
- **Running games across deploys:** each game is snapshotted to Postgres after every move, so a restart only interrupts players for a few seconds.
- **TLS:** the local overlay uses plain http. For a public overlay, set `PHX_SCHEME=https` / `PHX_URL_PORT=443`, add a cert-manager `Certificate` or Traefik ACME, and add an http→https redirect middleware.
- **Registry:** replace the `ctr import` step with CI pushing to GHCR, and point `images:` at `ghcr.io/<you>/millennium-quiz`.
- **Backups:** the Postgres data lives on a k3s `local-path` volume on the node. Add a CronJob running `pg_dump` before relying on it.
- **Yu-Gi-Oh! extras:** a card image cache (PVC or object storage) and an EDOPro/ygopro-core engine container would be added here as extra resources.

## Reviewing pull requests

Pull requests are reviewed by a separate Claude Code process that only knows
the repository: the PR, the manifests at its head commit, what they render to,
and the written docs (`REVIEWING.md`, this README, `AGENTS.md`). It never sees
the conversation in which the change was written. The entry point is
`bin/review-deploy-pr`; it is deliberately not called `review-pr`, which is the
quiz repo's script, so the two are never mixed up.

```sh
bin/review-deploy-pr 12          # one PR -> reviews/pr-<branch>.md
bin/review-deploy-open-prs       # every open PR, one process each, 3 at a time
REVIEW_MODEL=fable bin/review-deploy-pr 12 --force   # another model, review again
bin/review-selfcheck --force     # re-verify the isolation
bin/review-post 12 --dry-run     # preview the evaluated review as a PR comment
bin/review-cleanup               # archive reviews of merged or closed PRs
bin/review-dir                   # print the reviews directory (main checkout)
```

Guarantees, each checked by the scripts rather than promised:

- **Another model.** The reviewer's model family must differ from every Claude
  model in the PR's `Co-Authored-By` trailers; otherwise the review is refused.
  The model that actually answered is checked again and recorded in the
  review's header.
- **Isolation.** The reviewer runs inside a throwaway worktree of the head
  commit with the flags in `bin/review-flags`: only Read, Grep and Glob (no
  shell, no Write/Edit, no MCP), confined to that worktree, no settings, no
  saved session. `bin/review-selfcheck` verifies this with the installed Claude
  Code (tool list exactly Read, Grep, Glob; a marker file outside the working
  directory stays unread), before reviewing and after every Claude Code update.
- **Only this repository.** Every script sets `GH_REPO` from the `origin`
  remote, so `gh` cannot touch another repository.
- **Nothing touches a cluster.** The scripts never run `kubectl` against a
  cluster, only the offline `kubectl kustomize` (or `kustomize build`).
- **Real locations.** The diff the reviewer gets carries the head commit's line
  numbers, and every cited `path:line` is checked afterwards; impossible ones
  get a "Location check" warning in the review.

This repository has **no CI**, so there are no test results to hand over
(`.review/tests.md` says so). Instead the script runs the checks that are cheap
and offline itself, *before* the reviewer starts, and puts the output into
`.review/` so the reviewer needs no shell:

- `checks.md`: a tripwire over the added lines for credentials (names the file,
  never prints the value); `kubectl kustomize` of `base` and every overlay (the
  committed `secrets.env.example` stands in for the gitignored `secrets.env`
  in the throwaway checkout, and rendered `Secret` documents are dropped);
  images without a tag or with `latest`; `kubeconform` if it is installed.
- `rendered/head/<overlay>.yaml` and `rendered/diff-<overlay>.patch`: what each
  overlay renders to, and how that differs from the merge base.

`cluster/` holds plain manifests and is only read, not rendered.

There is **one current review per PR**, `reviews/pr-<branch>.md` (gitignored);
the reviews directory is always `reviews/` of the **main checkout**, even when
you run a command from a worktree (`bin/review-dir` prints it), so reviews do
not vanish with a worktree;
its header names the commit it covers. A PR is reviewed again only when it has
new commits (or with `--force`); the previous review moves to
`reviews/archive/` with the verdicts you ticked.

In Claude Code the same is available as `/review-deploy-pr <number>` and
`/review-deploy-open-prs`. Each finding has a "Your verdict" line: tick valid,
wrong or unsure. `/post-review <number>` (or `bin/review-post <number>`) then
posts the evaluated review to the PR: every finding with your verdict and
reason, and the commits after the review that address it. A fix commit names
its findings at the start of a line of its message, e.g. `- F2: ...` or
`- F1, F5: ...`. Posting again updates the same comment. Rejected findings can
go into "Decisions and known false positives" in `REVIEWING.md`.

The reviewer is defined in `.claude/agents/pr-reviewer.md`. The scripts need
`gh`, `claude`, `git`, `python3` and, for the manifest checks, `kubectl` or
`kustomize` and the Python module PyYAML (`pip install pyyaml` into the shared
venv `/home/authority/sandbox/.venv`, which must then be first in `PATH`, or
`sudo apt install python3-yaml` for the system python). Without PyYAML the
kustomization path check fails closed: every overlay is refused and nothing is
rendered; `bin/review-selfcheck` (run by `bin/review-deploy-pr`) stops with
this hint. `bwrap` (bubblewrap) is recommended: kustomize then runs without
network and with the home directory hidden. If bwrap is missing or does not
work here (the render is probed with the exact command line it will use), the
script falls back to `unshare` (network cut, filesystem NOT isolated) and, if
that fails too, to no sandbox at all; `checks.md` states which one was used,
and only the default load restrictor, no helm/plugins and the path check
protect the render in the last case. The helper logic is tested without Claude or GitHub (the end-to-end
test uses a fake `gh` and a fake `claude` in a temporary repository):

```sh
python3 -m unittest discover -s test/review
```
