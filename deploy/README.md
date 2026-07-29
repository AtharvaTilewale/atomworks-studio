# AtomWorks Studio production runbook

> This is an operations guide for maintainers deploying Studio. End users
> should start with the [main README](../README.md) or
> [website guide](../docs/WEB.md).

This runbook covers the complete path from a local browser build to the
permanent Kubernetes editor, an authenticated Brev GPU deployment, the warm
AlphaFold 3 (AF3) daemon, release automation, and production operations.

The production contract is intentionally split:

- The permanent Kubernetes service is the CPU web/API editor. Mol* and Patinae
  are explicit client-side renderers served by the same deployment.
- The currently implemented GPU provider runs Annotation Studio, RFD4, and AF3
  on one authenticated Brev VM. Jobs never require SLURM.
- A public Kubernetes site cannot yet submit directly to a separate Brev VM.
  That topology requires the remote worker bridge described below; do not
  expose an unauthenticated Brev port or Docker socket as a shortcut.

## 1. Quick local website

From the standalone Studio repository root, the shortest path on a machine
where the web dependencies have already run is:

```bash
RFPROTEINA_LIGHTWEIGHT_IMPORT=1 bash run.sh
```

Open either renderer explicitly:

- <http://127.0.0.1:8000/?viewer=molstar>
- <http://127.0.0.1:8000/?viewer=patinae>

The server rebuilds stale web assets automatically. A rebuild requires Node
20.19+ or 22.12+, pnpm, Rust with the `wasm32-unknown-unknown` target, and
`wasm-pack`.

For a clean first-time web setup:

```bash
rustup target add wasm32-unknown-unknown
cargo install --locked --version 0.13.1 wasm-pack
corepack enable
corepack prepare pnpm@11.16.0 --activate
pnpm --dir packages/web install --frozen-lockfile
RFPROTEINA_LIGHTWEIGHT_IMPORT=1 bash run.sh
```

To test the exact production container:

```bash
export GH_TOKEN="$(gh auth token)"
docker build --secret id=github_token,env=GH_TOKEN \
  --file deploy/Dockerfile \
  --tag atomworks-studio:local \
  .
docker run --rm --publish 8000:8000 atomworks-studio:local
```

The CPU image installs the published `atomworks[ml]` distribution and does not
copy an RFD4 source tree. The standalone provider package can be added with
`--build-arg ANNOTATION_STUDIO_INSTALL_RFD4_PROVIDER=true`; its `rfproteina`
dependency is resolved as a package, not copied from another checkout. Local editor mode
intentionally has GPU design disabled unless
`ANNOTATION_STUDIO_JOB_DIR`, `ANNOTATION_STUDIO_RFD4_ROOT`, and
`ANNOTATION_STUDIO_RFD4_COMMAND` are configured.

## 2. Source-control and release gate

Before enabling deployment:

1. Fetch `origin/main` and ensure the feature branch contains the latest main.
2. Commit the Studio application and deployment workflow.
3. Open a pull request and require the `CI` workflow.
4. Merge only after Python tests, both renderer E2E tests, the VSIX package,
   desktop plugin builds, and the production-container boot test pass.

The branch should report no commits missing from main:

```bash
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
```

The expected first number is branch-only commits and the second is commits
missing from `origin/main`. Before a merge, the second number must be `0`.

The Studio release version is the release authority. The web and
VS Code `package.json` versions must match it. A tagged release uses:

```bash
git tag -a v0.1.0 -m "AtomWorks Studio 0.1.0"
git push origin v0.1.0
```

## 3. Permanent Kubernetes editor

### Cluster prerequisites

Provision a managed Kubernetes cluster with:

- a default StorageClass supporting a 5 GiB ReadWriteOnce claim;
- an ingress controller, normally ingress-nginx;
- cert-manager and a production ACME `ClusterIssuer`;
- a public load balancer for the ingress controller;
- a namespace named `annotation-studio`;
- a least-privilege service account usable by GitHub Actions.

The kubeconfig used by Actions must reach the API server from a GitHub-hosted
runner. If the API is private, use an organization-managed self-hosted runner
inside the cluster network.

The deployment service account needs get/list/watch/create/update/patch/delete
on Deployments, Services, PersistentVolumeClaims, Ingresses, and Secrets in the
target namespace, plus permission to read rollout state. It does not need
cluster-admin.

### GHCR

The workflow publishes:

```text
ghcr.io/baker-laboratory/atomworks-studio:<git-sha>
```

Keep the SHA tag as deployment authority. `latest` is only a convenience tag.
For a private package, create a long-lived machine-user token limited to
`read:packages`; the workflow turns it into the namespace-local
`annotation-studio-ghcr` image-pull Secret.

### DNS and TLS

After installing the ingress controller:

```bash
kubectl --namespace ingress-nginx get service
```

Create an `A` record for an IP load balancer or a `CNAME` for a hostname load
balancer, for example:

```text
studio.example.com -> <ingress load balancer>
```

Wait for DNS propagation before enabling deployment. The configured
cert-manager issuer creates `annotation-studio-tls`; verify the resulting
Certificate is Ready.

### GitHub environment

Use the protected `annotation-studio` GitHub environment. Store credentials as
environment secrets, not in repository files or chat:

```bash
gh variable set ANNOTATION_STUDIO_HOST \
  --repo baker-laboratory/atomworks-studio \
  --env annotation-studio \
  --body studio.example.com

base64 -w0 deploy-kubeconfig.yaml |
  gh secret set KUBE_CONFIG_B64 \
    --repo baker-laboratory/atomworks-studio \
    --env annotation-studio

printf '%s' "$GHCR_READ_PACKAGES_TOKEN" |
  gh secret set GHCR_PULL_TOKEN \
    --repo baker-laboratory/atomworks-studio \
    --env annotation-studio
```

Required environment variables are:

- `ANNOTATION_STUDIO_NAMESPACE=annotation-studio`
- `ANNOTATION_STUDIO_HOST=<public DNS name>`
- `ANNOTATION_STUDIO_INGRESS_CLASS=nginx`
- `ANNOTATION_STUDIO_CERT_ISSUER=letsencrypt-prod`
- `ANNOTATION_STUDIO_TLS_SECRET=annotation-studio-tls`
- `ANNOTATION_STUDIO_FRAME_ANCESTORS='self'` plus exact approved embedding
  origins such as `https://build.nvidia.com` and `vscode-webview:`
- `GHCR_PULL_USERNAME=<machine-user login>`

This repository currently publishes images from the `v*` release workflow but
does not include a merge-to-`main` Kubernetes deployment workflow. Apply an
immutable release or commit image through operator-controlled automation:

```bash
export ANNOTATION_STUDIO_IMAGE=ghcr.io/baker-laboratory/atomworks-studio:<immutable-tag>
envsubst < deploy/kubernetes.yaml |
  kubectl --namespace annotation-studio apply -f -
kubectl --namespace annotation-studio rollout status deployment/annotation-studio
```

An external deployment workflow should perform the same apply, rollout,
revision check, and rollback sequence using a namespace-scoped identity. Do not
document automatic deployment as enabled until that workflow exists and has
been exercised.

The two stable links are:

```text
https://studio.example.com/?viewer=molstar
https://studio.example.com/?viewer=patinae
```

There is one server deployment, not two divergent applications. Patinae
requires WebGPU in the browser; Mol* requires WebGL. Neither silently falls
back to the other.

## 4. Brev GPU deployment

### Launchable

Create a Brev Launchable in VM Mode:

1. Select the standalone Studio Git repository.
2. Set `deploy/brev/launchable-setup.sh` as the setup script.
3. Disable Jupyter unless separately required.
4. Add an authenticated Secure Link named `studio` for port `8000`.
5. Use a GPU with enough VRAM for RFD4 and the intended input sizes.
6. Use persistent disk mounted under `/home/ubuntu/workspace`.
7. RFD4 is the `external/RFD4-Proteina-dev` submodule (from a `git clone
   --recursive`); the launcher uses it by default. Set `RFPROTEINA_ROOT` (or
   `ANNOTATION_STUDIO_RFD4_ROOT`) only to place RFD4 on a separate persistent disk.
8. Set `DATA_PATH` to a persistent directory containing the RFD4 checkpoints.
9. Set `BREV_RFD4_STATE_DIR` to
   `/home/ubuntu/workspace/rfd4-annotation-studio`.

The setup installs the web toolchain, runs the RFD4 checkout's `setup.sh`, creates
a restartable systemd service when systemd is available, starts the Studio, and
checks `/api/health`. On non-systemd hosts it falls back to `nohup`.

Brev Launchable setup values are available only during setup. The setup script
persists non-secret runtime paths for the service. Continuously required
credentials must come from a runtime secret manager; never print credentials or
store cloud keys in the generated service launcher.

### GPU layout

For a one-GPU pilot:

```bash
CUDA_VISIBLE_DEVICES=0
AF3_GPU_DEVICES=0
```

RFD4 and AF3 share GPU 0, which must be validated for peak VRAM.

For the recommended two-GPU search worker:

```bash
CUDA_VISIBLE_DEVICES=0
AF3_GPU_DEVICES=1
```

RFD4 uses GPU 0 and the AF3 daemon uses driver GPU 1. The Studio job manager
serializes RFD4 jobs, so one VM does not overcommit the model GPU.

### Persistent directories

Keep all durable data under `/home/ubuntu/workspace`:

```text
/home/ubuntu/workspace/rfd4-data/                 RFD4 checkpoints
/home/ubuntu/workspace/af3/af3.sif                AF3 runtime
/home/ubuntu/workspace/af3/scripts/                approved AF3 scripts
/home/ubuntu/workspace/af3/weights/                AF3 model parameters
/home/ubuntu/workspace/af3/jax-cache/              JAX compilation cache
/home/ubuntu/workspace/rfd4-annotation-studio/     jobs, sessions, logs
```

Stopping a Brev instance releases the GPU and clears loaded model state. Disk
data and the JAX cache persist, but the daemon must load again after restart.
Keeping the worker warm therefore requires a running Brev instance and
sufficient credits; a Launchable is a provisioning recipe, not an always-on
scheduler.

## 5. AF3 daemon

Only AlphaFold 3 is part of this production workflow. OpenFold-3/OF3 is not
configured or required.

The supported runtime is the existing Apptainer-backed warm daemon. Configure
all four variables or none:

```bash
AF3_APPTAINER_PATH=/home/ubuntu/workspace/af3/af3.sif
AF3_SCRIPTS_DIR=/home/ubuntu/workspace/af3/scripts
AF3_MODEL_DIR=/home/ubuntu/workspace/af3/weights
AF3_JAX_CACHE_DIR=/home/ubuntu/workspace/af3/jax-cache
```

Also configure any external data mounts:

```bash
APPTAINER_BIND_PATHS=/home/ubuntu/workspace:/home/ubuntu/workspace
```

`brev/start.sh` validates these paths and prestarts
`src/rfproteina/af3/af3_daemon.py`. The daemon loads AF3 once, retains the warm
worker, and receives fold requests over a mode-0600 Unix socket. RFD4
subprocesses inherit `AF3_LAUNCHER_SOCKET_TEMPLATE`, so generation, final
refolding metrics, and beam-search rewards reuse the daemon.

To validate AF3 independently on a configured GPU host:

```bash
source "${RFPROTEINA_ROOT}/.ipd/.env.local"
"${RFPROTEINA_ROOT}/scripts/run_af3_daemon_smoke.sh" --gpu 0
```

The smoke test starts the actual daemon and waits for the worker status to
become `ready`; it does not accept an early socket bind as model readiness.

On Brev:

```bash
sudo systemctl status rfd4-annotation-studio
sudo journalctl -u rfd4-annotation-studio -f
ls /home/ubuntu/workspace/rfd4-annotation-studio/af3-logs
```

Do not mount a Docker socket into the public Kubernetes editor. AF3 belongs on
the authenticated GPU worker.

## 6. AF3-guided generation and search

For final AF3 metrics without search, select an appropriate refolding tag such
as `unconditional_refolding`, `ppi_refolding`, `sm_binder_refolding`, or
`motif_scaffold_refolding`.

For beam search using AF3 pLDDT as reward:

```yaml
metrics_tags:
  - design
  - unconditional_refolding
search:
  rewards:
    reward_metrics_tags:
      - unconditional_refolding
    weights:
      oracle.ranking_score: 1.0
```

In the Studio, choose Search mode, select `unconditional_refolding`, set the
reward key to `oracle.ranking_score`, then set diffusion checkpoints, branching,
and beam width. Use `oracle.plddt_coi` only for inputs with a chain-of-interest
annotation. RFD4 writes generated CIFs and per-example metric CSV files.
The Studio collects and ranks outputs, returns generated and AF3-refolded CIFs,
shows numeric metrics, and exposes pLDDT/confidence-like CIF fields for viewer
coloring.

Use cheap inline metrics and pre-oracle filters before AF3 when possible. AF3
inside a branching search can multiply oracle calls quickly.

## 7. Public-site to Brev worker bridge

The same-host authenticated Brev deployment above is implemented. A permanent
public Kubernetes editor dispatching to a separate Brev worker is a separate
production phase and needs:

1. OIDC/SSO on the public Studio.
2. A persistent PostgreSQL or Redis-backed job queue.
3. S3-compatible storage for capability-scoped input CIFs, outputs, logs, and
   metric files.
4. A Brev worker that long-polls outbound over HTTPS, atomically claims jobs,
   downloads inputs, runs RFD4, and uploads outputs.
5. Short-lived, scoped worker credentials stored in a runtime secret manager.
6. Per-user quotas, concurrency limits, cancellation, retries, heartbeats,
   idempotency, audit logs, and cost attribution.
7. A provider implementation behind the existing design-job API so the browser
   contract and viewer remain unchanged.

Do not enable paid GPU submission anonymously. Session capability IDs protect
documents but are not identity, authorization, or a billing boundary.

## 8. Operations

### Health and rollout

```bash
curl --fail https://studio.example.com/api/health
kubectl --namespace annotation-studio rollout status deployment/annotation-studio
kubectl --namespace annotation-studio get certificate,ingress,pod,pvc
```

The public health response must report the expected Git revision. The Brev
worker additionally needs GPU, RFD4 checkpoint, AF3 daemon, disk-space, and
queue-heartbeat monitoring.

### Rollback

```bash
kubectl --namespace annotation-studio rollout undo deployment/annotation-studio
```

Alternatively deploy a known immutable GHCR SHA tag. Never roll back by moving
`latest`.

### Backups and retention

- Kubernetes stores authoritative session snapshots on its PVC for the normal
  two-hour session lifetime; undo history remains process-local.
- Brev keeps jobs, generated CIFs, metrics, and logs under
  `BREV_RFD4_STATE_DIR`, with seven-day application retention by default.
- Back up valuable designs to object storage before deleting a Brev instance.
- Alert before the persistent disk reaches 80% usage.

### Scaling

The shipped Kubernetes manifest deliberately uses one replica and `Recreate`
because sessions use a ReadWriteOnce PVC and process-local mutation locks.
Multiple replicas require shared transactional session storage and distributed
locking. The Brev worker likewise runs one RFD4 job at a time; horizontal GPU
scaling belongs in the external queue/worker phase.

## 9. Production acceptance checklist

- Feature branch contains the latest `origin/main`.
- All Studio files and the workflow are committed and reviewed.
- Both renderer E2E tests pass with no fallback.
- The production container boots and reports the build revision.
- DNS resolves to the ingress and TLS is Ready.
- The Kubernetes deploy identity is namespace-scoped.
- GHCR image pulling works without a personal broad-scope token.
- Operator-controlled deployment and rollback are exercised once.
- Brev state and all model artifacts live under persistent storage.
- The systemd Studio service restarts after a Brev VM reboot.
- AF3 daemon smoke reaches `ready`.
- A real one-design RFD4 generation completes and returns its CIF.
- A one-design AF3 refold returns confidence metrics and a refolded CIF.
- A bounded beam search returns ranked outputs.
- Authentication and quotas are enabled before remote paid GPU submission.
