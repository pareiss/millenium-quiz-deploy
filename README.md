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
cluster-wide parts: cert-manager, the ClusterIssuers and Traefik. (If you
earlier applied an older version of this overlay into `millennium-quiz`, it
replaced local's Secrets and config there: re-apply `overlays/local` to restore
them. Its Postgres volume keeps the password of its first start, see "Secrets".)

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
nothing), apply `cluster/traefik/helmchartconfig.yaml` once (it sets `Local`;
k3s restarts Traefik, with a few seconds of downtime for every site behind it,
local included) and check again. Then test the effect: make wrong logins at
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
- **TLS:** the local overlay uses plain http; `overlays/production` gets its certificate from cert-manager (see "Public deployment with Let's Encrypt") and runs in its own namespace.
- **Registry:** the app repository's CI already pushes to GHCR (`sha-<7>` tags); `overlays/production` pulls from there, `overlays/local` still uses `ctr import`.
- **Backups:** the Postgres data lives on a k3s `local-path` volume on the node. Add a CronJob running `pg_dump` before relying on it.
- **Yu-Gi-Oh! extras:** a card image cache (PVC or object storage) and an EDOPro/ygopro-core engine container would be added here as extra resources.
