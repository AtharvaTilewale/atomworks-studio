# Brev GPU launch

Brev is the GPU host, not a remote inference protocol. Annotation Studio and
RFD4 run on the same Brev instance; the browser submits capability-scoped jobs
to the Studio API, which queues one local GPU process at a time. No CIF leaves
the instance and there is no SLURM dependency.

The complete DNS, Kubernetes, GHCR, Brev, AF3, operations, and acceptance
checklist is in the [production runbook](../README.md).
After the service is ready, follow the [website guide](../../docs/WEB.md) to
load, annotate, submit, and compare designs.

## One-click Launchable

In the [Brev Launchable builder](https://docs.nvidia.com/brev/concepts/launchables):

1. Select this Git repository as the source and choose **VM Mode**.
2. Use `deploy/brev/launchable-setup.sh` as the setup script.
3. Disable Jupyter unless it is separately useful.
4. Add an authenticated **Secure Link** named `studio` for port `8000`.
5. Add required launch parameters `RFPROTEINA_ROOT`, pointing at a separate
   persistent RFD4 checkout, and `DATA_PATH`, pointing at persistent storage
   containing the RFD4 checkpoint. Optionally set `BREV_RFD4_STATE_DIR` to a
   persistent volume; on Brev VMs it defaults to
   `~/workspace/rfd4-annotation-studio`. To enable AF3, also add
   `AF3_APPTAINER_PATH`, `AF3_SCRIPTS_DIR`, `AF3_MODEL_DIR`, and
   `AF3_JAX_CACHE_DIR`.
6. Select a GPU profile with enough VRAM for the intended input size and create
   the Launchable.

The setup script runs the RFD4 checkout's `setup.sh`, pins the web toolchain,
builds the CUDA 12 RFD4 image once (default `latent-dev:cuda12`), installs a
restartable systemd service when available, prestarts the configured AF3 daemon,
and checks `/api/health`. The same source can be started with the Brev CLI:

```bash
brev start <repository-url> \
  --setup-repo . \
  --setup-path deploy/brev/launchable-setup.sh
```

Studio does not invoke `$RFPROTEINA_ROOT/scripts/launch_infer.sh` directly because that script
submits SLURM jobs. Both launch paths use the same underlying
`af3_daemon.py launch` lifecycle: `launch_infer.sh` emits it into the SLURM
batch script, while direct local/Brev services use
`$RFPROTEINA_ROOT/scripts/inference_runtime.sh`.

## Manual launch

1. Create a Brev GPU environment with enough VRAM for the longest intended
   input. Clone this Studio repository and RFD4 as separate checkouts on its
   persistent disk.
2. Run the RFD4 `setup.sh` once and stage the checkpoint under a persistent
   `DATA_PATH`.
3. Start the service:

   ```bash
   export RFPROTEINA_ROOT=/home/ubuntu/workspace/RFD4-Proteina-dev
   export DATA_PATH=/home/ubuntu/workspace/rfd4-data
   export BREV_RFD4_STATE_DIR=/home/ubuntu/workspace/rfd4-annotation-studio
   export BREV_RFD4_IMAGE=latent-dev:cuda12
   RFPROTEINA_CUDA_STACK=cuda12 \
     bash "${RFPROTEINA_ROOT}/scripts/build_latent_dev_docker.sh" "${BREV_RFD4_IMAGE}"
   bash deploy/brev/start.sh
   ```

4. Expose port `8000` through Brev's authenticated port URL and open it. Keep
   Brev authentication enabled; Annotation Studio intentionally does not
   duplicate platform identity.

The launch script enables the local RFD4 provider and stores CIF sessions, job
metadata, logs, generated CIFs, and metrics below
`~/workspace/rfd4-annotation-studio` on Brev. Set `BREV_RFD4_STATE_DIR` to
another persistent volume if needed.

## Persistent compilation cache

`start.sh` stores TorchInductor, Triton, and CUDA compilation artifacts below
`$BREV_RFD4_STATE_DIR/compile-cache` and mounts that directory into each RFD4
container. The first job compiles the required kernels; later jobs and service
restarts reuse them. Keep `BREV_RFD4_STATE_DIR` on the workspace disk or another
persistent volume. A cache can be rebuilt after model-shape, PyTorch/CUDA, image,
or GPU-architecture changes.

The Docker image and compile cache survive `brev stop` / `brev start` on a
stoppable VM. `brev delete` permanently removes the workspace disk, image, job
history, and cache; move the state directory to separately managed storage if it
must survive deletion.

## Instance lifecycle

Use a stoppable environment rather than a permanently running GPU when there
are long idle periods:

```bash
# Create from a Launchable in the currently selected Brev organization.
brev create rfd4-studio-cloud \
  --launchable <launchable-id> \
  --type l40s-48gb.1x \
  --mode vm \
  --jupyter=false \
  --stoppable

# Private access without publishing the service.
brev port-forward rfd4-studio-cloud --port 18000:8000

# Stop GPU billing while preserving the environment, image, and caches.
brev stop rfd4-studio-cloud

# Resume the same environment; systemd starts Studio automatically.
brev start rfd4-studio-cloud

# Destructive: removes the environment and its workspace disk.
brev delete rfd4-studio-cloud
```

## Search and oracles

Vanilla generation and rewards computed by RFD4 itself use the instance GPU
directly. AF3 refolding rewards require the separately licensed AF3 runtime and
weights documented in the production runbook. Select a non-refolding reward such as
`interchain_interactions.hbond_atom_pair_count_coi` when that oracle runtime is
not installed.

The web form covers common design count, batch, step, metric, beam, branching,
checkpoint, and reward settings. The YAML box accepts data-only model options;
server-controlled input/output paths, Hydra defaults, interpolation, and
arbitrary `_target_` values are rejected. This keeps a shared Brev URL from
becoming an arbitrary command runner.

## Production boundary

The permanent CPU website can edit CIFs but should keep design jobs disabled.
For GPU generation, either use this authenticated Brev deployment end-to-end or
deploy the same API/job contract behind the organization's authenticated GPU
service. A public Kubernetes pod must not mount a Docker socket merely to run
AF3; use a dedicated oracle service in that topology.
