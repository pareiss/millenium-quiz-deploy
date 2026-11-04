# millennium-quiz-deploy

Kubernetes manifests (kustomize) for [Millennium Quiz](../millennium-quiz).
The application code lives in its own repository; this repo only decides
*how* and *where* it runs.

```
base/                       environment-independent resources
  namespace.yaml            namespace millennium-quiz
  postgres/                 StatefulSet (postgres:17, 2Gi PVC) + headless Service
  backup/                   CronJob (daily pg_dump) + its own PVC, see "Backups"
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
overlays/production/        public host, Let's Encrypt certificate, own namespace
  kustomization.yaml        image tag, host, TLS, proxy hops, secret generation
  secrets.env.example       copy to secrets.env (gitignored)
cluster/                    applied once per cluster by hand, not part of an overlay
  cert-manager/             Let's Encrypt ClusterIssuers (staging + prod)
  traefik/                  HelmChartConfig: keep the client IP (externalTrafficPolicy: Local)
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

## Public deployment with Let's Encrypt

[cert-manager](https://cert-manager.io) requests the certificate from Let's
Encrypt and renews it; Traefik terminates TLS and the app runs plain http
behind it. The app redirects http to https (it trusts `X-Forwarded-Proto`,
which Traefik sets), sends HSTS and marks its session cookie `secure`, so no
Traefik redirect middleware is needed.

Prerequisites: a domain whose DNS record points at the cluster, and ports 80
and 443 reachable from the internet (port 80 is needed for the HTTP-01
challenge; otherwise use a DNS-01 solver in the issuer).

**Production has its own namespace, `millennium-quiz-production`**, with its
own Postgres StatefulSet, volume (`data-postgres-0`) and Secrets, so it can run
on the same k3s cluster as the local overlay (namespace `millennium-quiz`)
without taking it over. Use a different host name than local's. The overlay
changes everything that names the namespace (the Namespace itself,
`DNS_CLUSTER_QUERY`); `DATABASE_URL` uses the short host `postgres`, which
resolves in the pod's own namespace. Production and local share only the
cluster-wide parts: cert-manager, the ClusterIssuers and Traefik.

#### If you applied an earlier version of this overlay

An earlier version (commit a0bc33b) had no namespace of its own and was applied
into `millennium-quiz`, where it replaced local's Secrets and config. Skip this
if it was never applied. Otherwise (example commands for you to run, check
the output of each first):

```sh
# what is left of it in the local namespace
kubectl -n millennium-quiz get ingress,certificate,secret,order,challenge
kubectl -n millennium-quiz get ingress millennium-quiz -o yaml | grep -A4 -i 'tls\|cert-manager'

# restore local's own Secrets, config and Ingress (also drops the TLS block)
kubectl apply -k overlays/local

# delete only what cert-manager made for the old production host
kubectl -n millennium-quiz delete certificate millennium-quiz-tls   # if listed
kubectl -n millennium-quiz delete secret millennium-quiz-tls        # if listed
```

Do not delete the `postgres-0` pod, the volume `data-postgres-0` or the
`postgres` / `millennium-quiz` Secrets of local (the re-apply replaces them
with local's values; old generated ones with a different hash suffix are
unused and may stay). The local Postgres volume keeps the password of its first
start: if the old version started Postgres on a fresh volume with the
production password, local's `secrets.env` no longer matches it, see "Secrets"
(`ALTER USER`). Do not remove the cluster-wide ClusterIssuers or cert-manager;
production uses them.

```sh
# 1. cert-manager, once per cluster (v1.21.3 was the latest on 2026-10-24;
#    take the current one from https://github.com/cert-manager/cert-manager/releases)
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.21.3/cert-manager.yaml
kubectl -n cert-manager rollout status deploy/cert-manager-webhook

# 2. the issuers (edit the e-mail address first)
kubectl apply -f cluster/cert-manager/clusterissuers.yaml

# 3. the image must exist and be pullable (see below), then the overlay: your
#    domain in overlays/production/kustomization.yaml (3 places), the image
#    tag, fresh secrets, then deploy with the staging issuer
cp overlays/production/secrets.env.example overlays/production/secrets.env   # fill in new values
bin/secrets encrypt production
kubectl apply -k overlays/production
kubectl -n millennium-quiz-production get pods,certificate,challenge,order   # Ready=True after a minute or two

# 4. staging certificates are not trusted by browsers: once it works, change
#    the annotation to `letsencrypt-prod` in the overlay and apply again
#    (delete the secret `millennium-quiz-tls` so a real certificate is issued)
kubectl -n millennium-quiz-production delete secret millennium-quiz-tls
kubectl apply -k overlays/production
```

If the certificate stays pending, `kubectl -n millennium-quiz-production describe challenge`
shows why (usually DNS not pointing at the cluster, or port 80 blocked).

Check the certificate later with
`kubectl -n millennium-quiz-production get certificate,order,challenge` and
`kubectl -n millennium-quiz-production describe certificate millennium-quiz-tls`
(Ready=True, `Not After` date; cert-manager renews about 30 days before expiry).

### The image (before step 3)

The overlay pulls `ghcr.io/pareiss/millennium-quiz:<newTag>` from GitHub's
registry, not the locally imported image of the local overlay. The tag in the
overlay is a placeholder (`sha-changeme`); the pods stay in `ErrImagePull` /
`ImagePullBackOff` until it names an image that exists and the cluster can
pull. In the app repository, `.github/workflows/image.yml` builds the
`Dockerfile` and pushes it from `master` after CI and Smoke passed on that
commit: tag `sha-<7 characters of the commit>` (never moves; pin this one) and
`latest` (do not use). Nothing is pushed from branches or pull requests.

1. Find the tag: the app repository's *Actions* -> *Image* run of the commit
   you want to deploy (green), or its *Packages* -> `millennium-quiz` page.
   Put it into `newTag` in `overlays/production/kustomization.yaml`.
2. After the first push the package is **private**. Either make it public
   (*Packages* -> millennium-quiz -> *Package settings* -> visibility; the code
   is public anyway), or give the namespace a pull secret with a GitHub token
   that has `read:packages`:

   ```sh
   kubectl create namespace millennium-quiz-production   # step 3 applies it again, harmless
   kubectl -n millennium-quiz-production create secret docker-registry ghcr \
     --docker-server=ghcr.io --docker-username=<github user> --docker-password=<token>
   kubectl -n millennium-quiz-production patch serviceaccount default \
     -p '{"imagePullSecrets":[{"name":"ghcr"}]}'
   ```

   (The token is a secret: do not commit it.) The `describe` output in
   the next step shows whether the pull works.
3. After `kubectl apply`, verify the pull:

   ```sh
   kubectl -n millennium-quiz-production get pods                 # Running, not ImagePullBackOff
   kubectl -n millennium-quiz-production describe pod -l app.kubernetes.io/name=millennium-quiz | grep -A3 -i 'pull\|image'
   kubectl -n millennium-quiz-production get deploy millennium-quiz -o jsonpath='{.spec.template.spec.containers[0].image}'
   ```

   `Failed to pull image ... 401/403` means a private package without a pull
   secret; `manifest unknown` / `not found` means the tag does not exist.

### The client address and `TRUSTED_PROXY_HOPS`

The app's rate limits (login, e-mail, game creation, suggestions) identify a
visitor by `X-Forwarded-For` (`lib/millennium_quiz_web/client_ip.ex`).
`TRUSTED_PROXY_HOPS=n` makes it use the n-th entry from the right: each proxy
appends the address it got the connection from, the entries further left are
client-controlled and ignored. With `0` the header is ignored.

`1` is therefore correct exactly when Traefik is the only proxy in front of the
app **and the address Traefik sees is the visitor's**. On k3s that second part
is not guaranteed: Traefik sits behind ServiceLB, and with the Service default
`externalTrafficPolicy: Cluster` the source address can be replaced by a node
address (SNAT), so every visitor would count as one client. Cluster-internal
traffic on a single node often keeps the real address, a multi-node cluster or
some network setups do not; do not assume, check:

```sh
kubectl -n kube-system get svc traefik -o jsonpath='{.spec.externalTrafficPolicy}{"\n"}'
```

If it prints `Local`, the address is preserved. If it prints `Cluster` (or
nothing), `cluster/traefik/helmchartconfig.yaml` sets `Local`. Applying it is an
opt-in manual step and can overwrite an existing Traefik config: k3s reads one
`HelmChartConfig` named `traefik` in `kube-system` and the file replaces its
whole `valuesContent`. k3s may also ship its own
`/var/lib/rancher/k3s/server/manifests/traefik-config.yaml` on the node, which
it re-applies (edit that one instead if it exists). So look first:

```sh
kubectl -n kube-system get helmchartconfig traefik -o yaml
# none found: preview, then apply
kubectl diff -f cluster/traefik/helmchartconfig.yaml
kubectl apply --server-side -f cluster/traefik/helmchartconfig.yaml
# one exists: add `service.spec.externalTrafficPolicy: Local` to its
# valuesContent by hand (kubectl -n kube-system edit helmchartconfig traefik)
```

k3s restarts Traefik (a few seconds of downtime for every site behind it, local
included). Check the policy again afterwards. Then test the effect: make wrong logins at
`/admin/login` with one user name from your own connection until the app
answers with its rate-limit message (the limit per name and address is 5 in 5
minutes), then try the same name from a different network (a phone on mobile
data): it must get "wrong password", not the rate-limit message. If both are
blocked together, the app sees one address: the policy is
still `Cluster`, or a further proxy (a cloud load balancer, Cloudflare) is in
front of Traefik, in which case raise `TRUSTED_PROXY_HOPS` by one per proxy in
the overlay. With `Local`, the DNS record must point at the node that runs the
Traefik pod (single node: no difference). Before going public, also go
through the checklist "Before a public deployment" in the app's README.

## Production runbook

All commands are examples for the owner to run; nothing here has been run
against a cluster. Namespace `millennium-quiz-production`, database and user
`millennium_quiz`. The production Postgres volume is the only copy of the data.

**Back up first** (before every version bump, and regularly):

```sh
# 1. database dump to a local file (-Fc = compressed, restorable with pg_restore)
kubectl -n millennium-quiz-production exec postgres-0 -- \
  pg_dump -U millennium_quiz -Fc millennium_quiz > prod-$(date +%F).dump
ls -l prod-*.dump   # not empty?

# 2. the quiz content as JSON: /admin/backup "Download backup" in the browser
#    (only the questions, not games or accounts), or:
kubectl -n millennium-quiz-production exec deploy/millennium-quiz -c app -- \
  bin/millennium_quiz eval 'MillenniumQuiz.Release.export_quiz("/tmp/quiz-backup.json")'
kubectl -n millennium-quiz-production exec deploy/millennium-quiz -c app -- \
  cat /tmp/quiz-backup.json > quiz-backup-$(date +%F).json
```

A backup you have not restored is not a backup: test the dump once in a
throwaway database, never in the production one.

```sh
kubectl -n millennium-quiz-production exec -i postgres-0 -- createdb -U millennium_quiz restore_test
kubectl -n millennium-quiz-production exec -i postgres-0 -- \
  pg_restore -U millennium_quiz -d restore_test < prod-2026-11-02.dump
kubectl -n millennium-quiz-production exec postgres-0 -- \
  psql -U millennium_quiz restore_test -c 'select count(*) from schema_migrations'
kubectl -n millennium-quiz-production exec postgres-0 -- dropdb -U millennium_quiz restore_test
```

Keep dumps outside the cluster (and out of git: they contain accounts). Lesson
from the Gigalixir database that went read-only in October 2026: the only
reason the quiz survived was an earlier `/admin/backup` file; take it first.

**New version:**

```sh
# 1. backup (above). 2. find the new sha-<7> tag (app repo Actions -> Image, green)
# 3. put it into newTag in overlays/production/kustomization.yaml, then
kubectl kustomize overlays/production | grep 'image:'      # shows the new tag
kubectl apply -k overlays/production
kubectl -n millennium-quiz-production rollout status deploy/millennium-quiz
kubectl -n millennium-quiz-production get pods
kubectl -n millennium-quiz-production logs deploy/millennium-quiz -c migrate   # migrations ran?
```

The `migrate` init container runs the new version's migrations on every start,
before the app. The Deployment uses `Recreate`: the old pod stops first, so
expect a short outage.

**Rollback:**

```sh
# 1. pin the previous sha tag in newTag and apply again (keeps git as the truth) ...
kubectl apply -k overlays/production
# 2. ... or, quicker, undo the last Deployment revision (then also put the old
#    tag back in the overlay, or the next apply brings the new one back)
kubectl -n millennium-quiz-production rollout undo deploy/millennium-quiz
kubectl -n millennium-quiz-production rollout status deploy/millennium-quiz
```

Caveat: this only rolls back the code, not the database. If the new version
ran a migration (see the `migrate` log), the old code runs against the
migrated schema, which can fail or lose data if the migration is not
backwards compatible. Then restore the pre-update dump: scale the app to 0
(`kubectl -n millennium-quiz-production scale deploy/millennium-quiz --replicas=0`),
drop and recreate the database or restore into it
(`pg_restore --clean --if-exists -U millennium_quiz -d millennium_quiz < prod-<date>.dump`
via `exec -i postgres-0`), set the old tag, apply (which scales back to 1).
Games saved since the dump are lost.

**Logs and status:**

```sh
kubectl -n millennium-quiz-production logs deploy/millennium-quiz -f           # app
kubectl -n millennium-quiz-production logs deploy/millennium-quiz -c migrate   # migrations
kubectl -n millennium-quiz-production logs postgres-0
kubectl -n kube-system logs deploy/traefik                                     # requests, TLS
kubectl -n millennium-quiz-production get pods,certificate
kubectl -n millennium-quiz-production describe certificate millennium-quiz-tls
```

(The app logs to stdout; there are no log files. A restore's result from
`/admin/backup` is also written to the app log.)

## Backups

A CronJob `postgres-backup` (in `base/backup/`, so in both overlays) runs
`pg_dump --format=custom` every day at 03:17 UTC against the `postgres` Service,
with the password from the existing `postgres` Secret (no new secret values).
It uses the same `postgres:17` image as the StatefulSet, checks the dump with
`pg_restore --list`, writes `millennium_quiz-<UTC timestamp>.dump` to the
separate PVC `postgres-backup` (not the database volume). **It never deletes a
dump**: a corrupt database can still be dumped, so rotating automatically could
replace the last good dump with a bad one. Pruning is by hand (below). The
custom format is
compressed and restorable with `pg_restore`, also table by table; plain SQL
would be larger and could only be fed to `psql`. `concurrencyPolicy: Forbid`,
a 30 minute deadline, the last 3 successful and 3 failed Jobs are kept for
`kubectl logs`. Every run ends with a notification line in the job log
(`[discord mock] postgres-backup: ok ...`, or `FAILED ...`, or a warning when
the volume is `WARN_PERCENT` (80) full). It is only a **mock** of a Discord
message so far: nothing is sent. Hooking up a real webhook is the `notify()`
function at the top of the script in `base/backup/cronjob.yaml`.

**Nobody sees a failed job by themselves.** Until the notification is real,
check `kubectl -n $NS get cronjob postgres-backup -o jsonpath='{.status.lastSuccessfulTime}'`
now and then and before every deploy. If the target is full, every run fails:
free space by hand (below).

**What is backed up:** the whole database `millennium_quiz` (questions, cards,
accounts, saved games). **Not** the Secrets (they are in git, encrypted) nor
anything outside Postgres.

**Does not protect against:** losing the node or its disk. The default k3s
`local-path` storage puts the database volume *and* the backup volume on the
same node and the same disk. The PVC guards against a mistake or a corrupted
database volume, not against hardware, and **not against a full disk**:
`local-path` does not enforce the PVC's size, so dumps and `PGDATA` share the
free space, and a disk filled by dumps stops Postgres. Check the free space of
`/var/lib/rancher/k3s/storage` on the node against (dumps you keep) x (size of
one dump), prune, or put the PVC on other storage with a `storageClassName`. Copy dumps off the node regularly (below),
and keep them out of git (they contain accounts).

The app's own `/admin/backup` JSON export is a separate, different thing: it
holds only the quiz content (no games, no extra accounts) and is restored
through the app, not with `pg_restore`. Both are useful.

Settings: the schedule is `spec.schedule` and the warning level the
`WARN_PERCENT` env var in `base/backup/cronjob.yaml`; the PVC size is in
`pvc.yaml`. `overlays/production` patches the size (5Gi, a label on
`local-path`) and has commented places for a schedule and a `storageClassName`.

All commands below are examples for the owner; nothing here has been run
against a cluster. Use `-n millennium-quiz` for local.

```sh
NS=millennium-quiz-production

# state: schedule, last run, jobs
kubectl -n $NS get cronjob,job -l app.kubernetes.io/name=postgres-backup
kubectl -n $NS logs job/<job name>

# run one now (also the "fresh dump first" before a restore or an upgrade)
kubectl -n $NS create job --from=cronjob/postgres-backup postgres-backup-manual-$(date +%Y%m%d-%H%M)
kubectl -n $NS wait --for=condition=complete --timeout=10m job/postgres-backup-manual-<...>
kubectl -n $NS logs job/postgres-backup-manual-<...>   # "wrote ... (N bytes)" and the file list
```

(Delete a manual Job afterwards with `kubectl -n $NS delete job <name>`.)

### Prune old dumps by hand

Dumps pile up (about one per day). Use the throwaway pod from the next
section, but with `readOnly: false` on the volume mount, list the dumps and
delete the old ones (keep the newest ones and at least one you have checked):

```sh
kubectl -n $NS exec backup-shell -- ls -lh /backup
kubectl -n $NS exec backup-shell -- rm /backup/millennium_quiz-<timestamp>.dump
kubectl -n $NS delete pod backup-shell
```

### Copy a dump off the node

The dumps are in a PVC that no running pod has mounted. Start a throwaway pod
that does, copy, delete it:

```sh
kubectl -n $NS apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: backup-shell
spec:
  restartPolicy: Never
  securityContext: {runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: shell
      image: docker.io/library/postgres:17
      command: ["sleep", "3600"]
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}}
      volumeMounts: [{name: backup, mountPath: /backup, readOnly: true}]
  volumes:
    - name: backup
      persistentVolumeClaim: {claimName: postgres-backup}
EOF
kubectl -n $NS wait --for=condition=Ready pod/backup-shell
kubectl -n $NS exec backup-shell -- ls -l /backup
kubectl -n $NS cp backup-shell:/backup/millennium_quiz-<timestamp>.dump ./prod-<timestamp>.dump
ls -l prod-*.dump     # not empty?
kubectl -n $NS delete pod backup-shell
```

(The PVC is ReadWriteOnce on a single node, so the pod can share it with a
running backup Job there; avoid doing it during the 03:17 run anyway.)

### Restore into a fresh database

Restore into a *new* database next to the old one and swap by renaming, so the
current data is not overwritten until the restored one is checked.

```sh
# 0. take a fresh dump first (above), copy it off. Pick the dump to restore;
#    copy it to your machine as restore.dump (see "Copy a dump off the node").

# 1. stop the app (it holds connections and writes); Postgres keeps running.
#    Suspend the backup CronJob too: a dump running now holds a connection to
#    the database and would stop the rename in step 4
kubectl -n $NS scale deploy/millennium-quiz --replicas=0
kubectl -n $NS patch cronjob postgres-backup -p '{"spec":{"suspend":true}}'
kubectl -n $NS get job -l app.kubernetes.io/name=postgres-backup   # none may be running

# 2. create an empty database and load the dump into it. The dump has the
#    owner millennium_quiz, the same user that is restoring.
kubectl -n $NS exec postgres-0 -- createdb -U millennium_quiz millennium_quiz_restored
kubectl -n $NS exec -i postgres-0 -- \
  pg_restore -U millennium_quiz -d millennium_quiz_restored --no-owner --exit-on-error < restore.dump

# 3. check it
kubectl -n $NS exec postgres-0 -- \
  psql -U millennium_quiz millennium_quiz_restored -c 'select count(*) from schema_migrations'

# 4. swap: connect to the `postgres` maintenance database, because a database
#    cannot be renamed while you are connected to it
kubectl -n $NS exec postgres-0 -- psql -U millennium_quiz postgres \
  -c 'ALTER DATABASE millennium_quiz RENAME TO millennium_quiz_old' \
  -c 'ALTER DATABASE millennium_quiz_restored RENAME TO millennium_quiz'

# 5. start the app again; the migrate init container applies migrations that
#    are newer than the dump
kubectl -n $NS scale deploy/millennium-quiz --replicas=1
kubectl -n $NS rollout status deploy/millennium-quiz
kubectl -n $NS logs deploy/millennium-quiz -c migrate
kubectl -n $NS patch cronjob postgres-backup -p '{"spec":{"suspend":false}}'   # backups on again

# 6. when everything works, drop the old one (not before)
kubectl -n $NS exec postgres-0 -- dropdb -U millennium_quiz millennium_quiz_old
```

If step 4 fails with "being accessed by other users", something is still
connected: check that the app really is at 0 replicas and that no backup Job is
running (step 1). Restoring a dump from an
older app version into a newer app works (migrations run on start); the other
way round, restore only with the matching older image tag. If the whole volume
is gone (new node), start with an empty cluster deployment first (the
`First deployment` steps), then restore as above.

## Day-to-day (local overlay)

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
- **TLS:** the local overlay uses plain http; `overlays/production` gets its certificate from cert-manager (see "Public deployment with Let's Encrypt") and runs in its own namespace.
- **Registry:** the app repository's CI already pushes to GHCR (`sha-<7>` tags); `overlays/production` pulls from there, `overlays/local` still uses `ctr import`.
- **Backups:** done: a daily `pg_dump` CronJob (dumps are never deleted automatically, pruning is by hand; the Discord notification is only a mock), see "Backups". It lands on the same node as the database; copying dumps off the node is still manual (an off-site copy, e.g. an object store, would be the next step).
- **Yu-Gi-Oh! extras:** a card image cache (PVC or object storage) and an EDOPro/ygopro-core engine container would be added here as extra resources.

## Reviewing pull requests

Pull requests are reviewed by a separate Claude Code process that only knows
the repository: the PR, the manifests at its head commit, what they render to,
and the written docs (`REVIEWING.md`, this README, `AGENTS.md`). It never sees
the conversation in which the change was written. The entry point is
`bin/review-deploy-pr`; it is deliberately not called `review-pr`, which is the
quiz repo's script, so the two are never mixed up.

```sh
bin/review-deploy-pr 12          # one PR -> reviews/pr-<number>-<branch>.md
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

There is **one current review per PR**, `reviews/pr-<number>-<branch>.md` (gitignored);
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
`kustomize` and the Python module PyYAML (`pip install pyyaml` into the python3 that is first in
`PATH`, e.g. a venv, or `sudo apt install python3-yaml` for the system python). Without PyYAML the
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
