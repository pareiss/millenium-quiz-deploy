# Reviewing millennium-quiz-deploy

What a reviewer needs to know about this repository, and what to look for.
Read this first, then `README.md` (layout, first deployment, secrets) and
`AGENTS.md` (agent rules and the PR process).

This file is the only knowledge shared between whoever writes a change and
whoever reviews it. If a review keeps getting something wrong, the fix belongs
here (see "Decisions and known false positives" at the end).

## The repo in short

- Kubernetes manifests (kustomize) that run [Millennium Quiz](https://github.com/pareiss/millennium-quiz)
  (an Elixir/Phoenix LiveView app) on the owner's own **k3s** cluster. The app's
  code lives in its own repository; this repo only decides *how* and *where* it runs.
- `base/`: environment-independent resources: namespace, Postgres StatefulSet
  (2Gi PVC, `local-path`), the app Deployment (1 replica, `Recreate`, a
  `migrate` init container that runs migrations and creates the first admin),
  Services, ConfigMap, Ingress (Traefik). The base image name is a placeholder
  (`millennium-quiz`) that overlays replace.
- `overlays/<name>/`: one per environment. `local` is plain http on
  `millennium-quiz.localhost`. `production` is the public deployment with a
  Let's Encrypt certificate (cert-manager); it comes with the Let's Encrypt
  pull request and may not be on `master` yet. Each overlay owns its image,
  host, secrets and patches.
- `cluster/`: cluster-wide objects applied once by hand, not part of an overlay
  (the cert-manager `ClusterIssuer`s). Plain manifests, not a kustomization.
- `bin/secrets`, `.githooks/pre-commit`, `.sops.yaml`: secrets are committed
  only as SOPS + age encrypted `overlays/<name>/secrets.enc.env`; the plaintext
  `secrets.env` is gitignored.
- There is **no CI** and nothing here can be tested by running it. What the
  review script checks offline (secret tripwire, `kubectl kustomize` of every
  overlay, rendered diff, unpinned images, kubeconform if installed) is in
  `.review/checks.md`. Everything else is judged by reading.
- **Nothing is applied to a cluster by an agent or by a review.** Deploying is
  the owner's job, by hand (`kubectl apply -k overlays/<name>`).

## Invariants: flag any change that breaks one

### Secrets and credentials
- **No real secret in git, ever**: no passwords, `SECRET_KEY_BASE`, release
  cookies, database URLs with passwords, admin passwords, tokens, registry
  credentials, kubeconfigs, age private keys (`AGE-SECRET-KEY-…`), TLS keys.
  Plaintext values are allowed only in `*.example` files, as obvious
  placeholders (`change-me…`).
- `overlays/*/secrets.env` and `postgres.env` stay gitignored. Every
  `*.enc.env` must be SOPS-encrypted (values `ENC[…]`, a `sops_version` line);
  `.sops.yaml` may only list **public** age keys.
- Secrets reach pods only through the generated Secrets (`secretGenerator`
  with `envs`) and `secretKeyRef`/`envFrom`, never as literal `value:` in a
  manifest or ConfigMap.
- A new key a pod needs goes into `secrets.env.example` of every overlay that
  must define it, with a placeholder and a comment.
- A review must never quote a secret value; name the file and line.

### Base vs. overlays
- `base/` holds nothing environment-specific: no real domain, no registry
  host, no TLS issuer, no proxy-hop count tuned for one network. Defaults in
  `base/` are safe placeholders (`*.example.com`) that an overlay must replace.
- Anything that differs between local and production is a patch in the
  overlay. A host appears in the ConfigMap `PHX_HOST`, the Ingress rule and (if
  TLS) the Ingress `tls.hosts`; a change to one without the others is a bug.
- A new overlay file or resource must be reachable from a `kustomization.yaml`;
  an overlay must build (`.review/checks.md`).
- `cluster/` objects are applied once per cluster, not by an overlay.

### Images and workloads
- Every image has an explicit tag or digest. **No `latest`**, no untagged
  image. Overlays pin the app image with `images:` (`newName`/`newTag`); a tag
  that changes is a deploy of a new app version and the PR says which one.
  The third-party `postgres` image keeps a major version tag (`17`); a major
  bump needs a data migration plan (the data directory is not compatible).
- Pods set resource `requests` and `limits` (at least memory), run as non-root
  where the image allows (`runAsNonRoot`, `allowPrivilegeEscalation: false`),
  and keep liveness/readiness/startup probes. Removing or loosening a probe
  needs a reason.
- The **app runs with 1 replica and `strategy: Recreate`**: every running game
  is a process in a node-local Registry. Raising `replicas` or switching to
  `RollingUpdate` is wrong until the app does clustering and global
  registration (the headless Service and `DNS_CLUSTER_QUERY` are only prepared).
- The `migrate` init container runs migrations before the app starts and may be
  retried by Kubernetes while Postgres starts; it must stay idempotent and use
  the same image tag as the app container.

### Database and rollout safety
- **Postgres is the only copy of the data.** The StatefulSet, its PVC
  (`volumeClaimTemplates`) and `PGDATA` must not be renamed, resized down,
  recreated or pointed at another volume by a routine change; `storageClassName`
  and `accessModes` of an existing claim cannot be changed in place.
- Postgres reads `POSTGRES_PASSWORD` only when it initializes an *empty*
  volume. Changing it in `secrets.env` does not change an existing database
  (the pods restart with a password that no longer matches): such a change
  needs an `ALTER USER` first, and the PR must say so.
- A change that restarts, recreates or migrates the database or the app needs
  the deploy notes in the PR: **take a backup first** (the app's admin backup
  page, `/admin/backup`, exports the quiz content; games and extra accounts are
  not in it; or `pg_dump` from `postgres-0`, or a manual Job from the backup CronJob), then deploy, then check
  `kubectl -n millennium-quiz get pods` and the `migrate` container's logs.
- **Rollback must be possible and written down**: the previous image tag, and
  for a migration that is not backwards compatible, the backup to restore. A
  PR that bumps the app image across a migration says whether the old image
  still runs on the migrated database.
- `CARD_NAMES_REFRESH`: the app can fill a list of ~14,000 card names on its
  own. On the Gigalixir deployment it is set to `false` because the free
  database hit its 10,000-row cap and went read-only. That limit does not apply
  to our own Postgres, but a PR that sets or removes the flag must say why, and
  must not ship a database size smaller than the data it will hold.
- Running games are snapshotted to Postgres after every move, so a restart only
  interrupts players for a few seconds; that is the accepted downtime of a deploy.

### TLS, ingress and cert-manager (the Let's Encrypt plan)
- TLS is terminated by Traefik with a certificate that **cert-manager** gets
  from Let's Encrypt; the app speaks plain http behind it and redirects http to
  https itself (`X-Forwarded-Proto`), sends HSTS and a `secure` cookie. So: no
  Traefik redirect middleware is needed, and the base keeps `PHX_SCHEME: https`
  and `PHX_URL_PORT: "443"` as defaults; only the local overlay uses http.
- The production Ingress carries the annotation
  `cert-manager.io/cluster-issuer` naming an existing issuer from
  `cluster/cert-manager/clusterissuers.yaml` (`letsencrypt-staging` or
  `letsencrypt-prod`), and `spec.tls[].hosts` plus `secretName` (cert-manager
  creates that Secret).
- **Staging first, then prod.** A PR that switches to `letsencrypt-prod` is
  the owner's step after staging worked; a PR must not default a new host to
  prod (rate limits). Staging certificates are not trusted by browsers, which
  is expected and not a finding.
- The issuers use HTTP-01 through the `traefik` ingress class; that needs the
  domain to resolve to the cluster and port 80 reachable. Changing the solver
  or the class needs the same justification. The ACME e-mail in the issuers is
  a placeholder until the owner sets it; do not put a real address in a PR
  unless the owner asked for it.
- `TRUSTED_PROXY_HOPS` is the number of proxies in front of the app (Traefik =
  1; add one per further proxy or load balancer). The app's rate limits (login,
  e-mail, game creation) read the client address from `X-Forwarded-For` with
  it: too low counts everyone as the proxy, too high lets clients spoof their
  address. On k3s the address Traefik sees is the real one only with
  `externalTrafficPolicy: Local`.
- Do not expose Postgres (no Ingress, NodePort or LoadBalancer for it). The
  `postgres` Service stays cluster-internal.
- cert-manager itself (a pinned upstream release) is installed by hand once per
  cluster; its manifest is not vendored here.

### Scripts and process
- Nothing in this repo applies, deletes or edits resources in a cluster on its
  own: no script, hook or CI step runs `kubectl apply`/`delete`/`patch`,
  `helm install` or similar. Documentation may tell the *owner* to.
- `bin/secrets` and `.githooks/pre-commit` keep refusing plaintext secrets.
  A change that weakens the hook (a new path it no longer checks) is a finding.
- The README is the runbook: a changed or added deploy step (new Secret key,
  new cluster-wide object, new env var) is documented in the same PR.

## What is not worth flagging
- Formatting, YAML key order, comment wording, README prose style.
- Resource numbers (CPU/memory) that are plausible for a small hobby cluster,
  unless a limit is missing or clearly below what the app/Postgres needs.
- Placeholder values in `*.example` files and in `base/` that are meant to be
  replaced (`quiz.example.com`, `you@example.com`, `change-me…`).
- A missing CI, missing automated tests, or "no Helm chart / no GitOps tool":
  this is a deliberate, small setup.
- Things listed under "Decisions" below, unless the change contradicts them.

## Decisions and known false positives

Add an entry when a review finding is rejected for a reason that will come up
again, or when a design decision should stop being questioned.

- **Placeholder domain and e-mail in committed manifests.** `quiz.example.com`
  and `you@example.com` are on purpose until the owner has a domain; the owner
  fills them in at deploy time. Don't flag them, but do flag a real-looking
  one that appears without the owner having asked.
- **One app replica with `Recreate`, no PodDisruptionBudget, no autoscaling.**
  Deliberate (see invariants). Don't suggest `RollingUpdate` or more replicas.
- **Postgres as a StatefulSet in the cluster, not a managed database.**
  The owner's decision. Backups are a daily `pg_dump` CronJob (`base/backup/`,
  README "Backups") into a PVC on the same node; the off-node copy is manual.
  Don't flag the absence of an off-site backup in a PR that doesn't touch
  backups. A PR that does touch them: the image stays the StatefulSet's
  Postgres version, the credentials come from the `postgres` Secret, the dump
  PVC stays separate from the database volume, the job deletes only dumps older
  than `RETENTION_DAYS` and never the newest `MIN_KEEP`, and only after a
  verified dump, and the README restore steps still match.
- **The image is imported into k3s by hand (`ctr images import`) for the local
  overlay.** A registry (`ghcr.io/pareiss/millennium-quiz`, built by the app
  repo's CI) is the production plan; the local overlay's `localhost/…` image is
  expected.
- **`.review/checks.md` uses placeholder secrets.** To render overlays the
  script copies `secrets.env.example` in the review checkout. Generated Secret
  names and hash suffixes in `.review/rendered/` therefore differ from a real
  deploy; don't flag them.
- **Fake credentials in `test/review/`.** The tests of the secret tripwire
  contain deliberately fake values. `bin/review-tools` allowlists a hit only if
  the file is under `test/review/`, the kind is age key, URL credentials or
  assigned password/key, and the value is obviously fake (contains dummy, fake,
  fixture or not-real; an age key of one repeated character). Those show up in
  `checks.md` as "allowlisted fixture" lines. A real-looking value in
  `test/review/`, or a fake-looking one anywhere else, is still a finding.
