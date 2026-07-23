# Brev GPU launch

Brev is the GPU host, not a remote inference protocol. Annotation Studio and
RFD4 run on the same Brev instance; the browser submits capability-scoped jobs
to the Studio API, which queues one local GPU process at a time. No CIF leaves
the instance and there is no SLURM dependency.

The complete DNS, Kubernetes, GHCR, Brev, AF3, operations, and acceptance
checklist is in the [production runbook](../deploy/README.md).

## One-click Launchable

In the [Brev Launchable builder](https://docs.nvidia.com/brev/concepts/launchables):

1. Select this Git repository as the source and choose **VM Mode**.
2. Use `apps/annotation-studio/brev/launchable-setup.sh` as the setup script.
3. Disable Jupyter unless it is separately useful.
4. Add an authenticated **Secure Link** named `studio` for port `8000`.
5. Add required launch parameter `DATA_PATH`, pointing at persistent storage
   containing the RFD4 checkpoint. To enable AF3, also add
   `AF3_APPTAINER_PATH`, `AF3_SCRIPTS_DIR`, `AF3_MODEL_DIR`, and
   `AF3_JAX_CACHE_DIR`.
6. Select a GPU profile with enough VRAM for the intended input size and create
   the Launchable.

The setup script reuses the repository `setup.sh`, pins the web toolchain,
installs a restartable systemd service when available, prestarts the configured
AF3 daemon, and checks `/api/health`. The same source can be started with the
Brev CLI:

```bash
brev start <repository-url> \
  --setup-repo . \
  --setup-path apps/annotation-studio/brev/launchable-setup.sh
```

## Manual launch

1. Create a Brev GPU environment with enough VRAM for the longest intended
   input. Clone this repository with submodules on its persistent disk.
2. Run the repository `setup.sh` once and stage the RFD4 checkpoint under a
   persistent `DATA_PATH`.
3. Start the service:

   ```bash
   export DATA_PATH=/home/ubuntu/workspace/rfd4-data
   export BREV_RFD4_STATE_DIR=/home/ubuntu/workspace/rfd4-annotation-studio
   bash apps/annotation-studio/brev/start.sh
   ```

4. Expose port `8000` through Brev's authenticated port URL and open it. Keep
   Brev authentication enabled; Annotation Studio intentionally does not
   duplicate platform identity.

The launch script enables the local RFD4 provider and stores CIF sessions, job
metadata, logs, generated CIFs, and metrics below
`~/workspace/rfd4-annotation-studio` on Brev. Set `BREV_RFD4_STATE_DIR` to
another persistent volume if needed.

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
