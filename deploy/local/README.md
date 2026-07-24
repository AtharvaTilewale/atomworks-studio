# Local RFD4 Studio prototype

This mode runs the complete prototype on one GPU workstation:

```text
browser -> Annotation Studio API -> local generate.py -> generated CIF + metrics -> browser
```

It uses the same web UI and design-job API as Brev, but invokes RFD4 on the
workstation. No SLURM, GitHub Actions, remote queue, or Brev environment is
involved.

This guide configures the GPU runtime. Once the service is ready, use the
[website guide](../../docs/WEB.md) for the browser workflow.

## Prerequisites

- The standalone Studio repository setup and web dependencies are installed.
- RFD4 is a separate checkout. Set `RFPROTEINA_ROOT` (or
  `ANNOTATION_STUDIO_RFD4_ROOT`) to its absolute path. When neither is set,
  the local launcher checks the sibling `../RFD4-Proteina-dev` checkout.
- `nvidia-smi` works and the Python environment reports CUDA.
- `$RFPROTEINA_ROOT/.ipd/.env.local` sets an absolute `DATA_PATH`.
- The release denoiser and autoencoder checkpoints exist under `DATA_PATH`.

Download the public prototype checkpoints when needed:

```bash
RFPROTEINA_ROOT=/absolute/path/to/RFD4-Proteina-dev
bash "${RFPROTEINA_ROOT}/scripts/download_example_data.sh" \
  /absolute/path/to/data_path
```

## Start

From the AtomWorks Studio repository root:

```bash
RFD4_GPU_DEVICE=0 \
RFPROTEINA_ROOT=/absolute/path/to/RFD4-Proteina-dev \
  bash deploy/local/start.sh
```

The launcher verifies that the host Python environment can initialize CUDA
before exposing the design button. If the workstation driver is older than the
repository's CUDA 13 stack, either upgrade the NVIDIA driver or build the
supported CUDA 12 image and use it only for RFD4:

```bash
RFPROTEINA_CUDA_STACK=cuda12 \
  bash "${RFPROTEINA_ROOT}/scripts/build_latent_dev_docker.sh" latent-dev:cuda12

LOCAL_RFD4_IMAGE=latent-dev:cuda12 \
RFD4_GPU_DEVICE=0 \
ANNOTATION_STUDIO_RFD4_ONLY=1 \
RFPROTEINA_ROOT=/absolute/path/to/RFD4-Proteina-dev \
  bash deploy/local/start.sh
```

The container receives only the repository, read-only `DATA_PATH`, and the
local Studio job directory. The browser/API still runs directly on the host.
`ANNOTATION_STUDIO_RFD4_ONLY=1` explicitly ignores AF3 paths inherited from
`$RFPROTEINA_ROOT/.ipd/.env.local`; omit it when all four supported Apptainer
paths are ready.
Studio cannot call `$RFPROTEINA_ROOT/scripts/launch_infer.sh` directly because
that launcher requires SLURM. It sources
`$RFPROTEINA_ROOT/scripts/inference_runtime.sh`, which shares the same
underlying `af3_daemon.py launch` lifecycle without `sbatch`/`srun`.

Open:

- <http://127.0.0.1:8000/?viewer=molstar>
- <http://127.0.0.1:8000/?viewer=patinae>

When this workstation is registered as `305e0a2-lcedt`, another
Brev-authenticated machine can open a private tunnel:

```bash
brev port-forward 305e0a2-lcedt --port 8000:8000
```

The Studio remains bound to loopback; the Brev SSH tunnel is the only remote
entry point. Stop the foreground launcher with `Ctrl-C`. Check readiness with:

```bash
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/design/schema
```

Upload or create an annotated CIF, open **Generate with RFD4**, choose a small
prototype job such as one design and a low step count, and submit. The server
serializes local jobs, writes the authoritative input CIF and safe Hydra config,
runs `src/rfproteina/generate.py`, collects generated/refolded CIFs and metrics,
then opens the top-ranked result in the existing viewer.

Ready-to-upload PPI, MCSA, and DISCO-style ligand-binder inputs live in
`examples/`. Their README records the canonical source
benchmarks and regeneration commands. A one-step run verifies plumbing, not
design quality.

State is retained under:

```text
~/.local/share/rfd4-annotation-studio-local/
```

Override it with `LOCAL_RFD4_STUDIO_STATE_DIR`. The launcher also keeps
TorchInductor, Triton, and CUDA compilation artifacts under
`compile-cache/` there and mounts them into `LOCAL_RFD4_IMAGE`. Compilation is
therefore reused across jobs, containers, and Studio restarts. The cache may
rebuild after model-shape, PyTorch/CUDA, image, or GPU-architecture changes.

## AF3

The local prototype defaults to RFD4-only generation and inline metrics. Set all
four `AF3_APPTAINER_PATH`, `AF3_SCRIPTS_DIR`, `AF3_MODEL_DIR`, and
`AF3_JAX_CACHE_DIR` values in `$RFPROTEINA_ROOT/.ipd/.env.local` after installing Apptainer and
the licensed AF3 runtime assets. `local/start.sh` then starts and supervises a
warm AF3 daemon automatically (GPU 1 by default; override with
`AF3_GPU_DEVICES`) and exports its socket to the Studio process. Partial AF3
configuration fails early, and AF3 metric choices remain hidden when it is not
configured.

Use `$RFPROTEINA_ROOT/scripts/run_af3_daemon_smoke.sh --gpu 1` first to verify that the worker
reaches `ready`. The two-GPU local default keeps RFD4 on GPU 0 and AF3 warm on
GPU 1.

## GitHub Actions and CoreWeave

GitHub Actions is appropriate for CI smoke jobs and deployment, not as an
interactive inference queue. Interactive submissions should go to a durable,
authenticated CoreWeave/Brev worker service with a persistent queue and object
storage. Starting an Actions workflow per browser request introduces cold-start
latency, weak cancellation, repository-scoped credentials, Actions concurrency
limits, and an unsuitable billing/security boundary.

For a temporary internal demonstration, a manually triggered workflow can place
an input CIF in object storage, run one bounded job on the existing self-hosted
CoreWeave runner, upload the outputs, and call back to the Studio. It should not
be connected directly to an anonymous public endpoint. The production form is
the outbound-polling CoreWeave worker described in `deploy/README.md`.
