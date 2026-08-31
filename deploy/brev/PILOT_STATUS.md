# Authenticated Brev pilot status

Last full GPU acceptance: 2026-07-25

## Current re-verification

On 2026-08-24, both inference assets selected by the pinned RFD4 config were
anonymously readable from `rfd4-proteina-public-e04a`: the release denoiser
(4,169,897,141 bytes) and autoencoder (4,100,184,649 bytes). The verification
command is:

```bash
uv run python scripts/check_rfd4_assets.py \
  --rfd4-root external/RFD4-Proteina-dev --bucket-only
```

The recorded Secure Link returned HTTP 404 on 2026-08-24, and this workstation
does not currently have an authenticated Brev CLI. Treat the endpoint and
instance details below as the last accepted pilot record, not evidence of a
currently running deployment. Before inviting users, an operator must start or
recreate the instance and rerun `make accept-brev` plus the clean-Launchable
cycle.

## Re-verification handoff

To let a maintainer run the complete live acceptance, do **not** send a Brev
password, browser cookie, SSH private key, GitHub token, AF3 weight credential,
or cloud key in chat. Instead, provide one of these controlled access paths:

1. Install the Brev CLI on the maintainer workstation and complete its normal
   interactive device/browser login there, then select the
   `rdf4-proteina-enablement` organization; or
2. Add the maintainer's approved account to that organization with permission
   to inspect, start, stop, SSH/exec, and manage Secure Links for the existing
   `rfd4-studio-cloud` instance; or
3. Have the current instance owner start the instance and open a temporary,
   authenticated terminal session on it. The maintainer needs no persistent
   secret if the session already has the required repository access.

Before the run, the operator must explicitly approve use of the selected GPU
profile and credit spend, confirm that the persistent workspace is retained,
and give the current Secure Link URL (or authorize creation of one). For the
one-GPU RFD4 acceptance, public RFD4 checkpoint access is sufficient; AF3 must
remain disabled. A two-GPU AF3 acceptance additionally needs the separately
licensed AF3 runtime and weights already mounted on the worker.

With one of those paths, the maintainer will run the existing instance and
clean-Launchable checks, save their artifacts under the persistent state
directory, verify authenticated browser access in both renderers, perform a
real design and output-open round trip, and test restart persistence. The
commands and acceptance criteria are in [PILOT_PLAN.md](PILOT_PLAN.md).

## Endpoint and ownership

- Studio URL: <https://atomworks-studio-k72mdjjr8.apps.run.brev.nvidia.com>
- Brev organization: `rdf4-proteina-enablement`
- Instance: `rfd4-studio-cloud` (`k72mdjjr8`)
- Hardware: one NVIDIA L40S 48 GB GPU
- Operator: `kdidi@nvidia.com`
- Access: Brev Secure Link with browser authentication and an explicit email
  allowlist. Anonymous access must remain disabled.
- Stop/start owner: the operator above. Stop the instance during extended idle
  periods; do not delete it unless the persistent workspace has been backed up.

The Secure Link is the only supported browser entry point. Port `8000` is not
exposed as a raw TCP/UDP or cloud-firewall port.

## Deployed layout

```text
/home/ubuntu/workspace/atomworks-studio/
/home/ubuntu/workspace/RFD4-Proteina-studio/
/home/ubuntu/workspace/rfd4-data/
/home/ubuntu/workspace/rfd4-annotation-studio/
```

The accepted RFD4 checkout was pinned at
`0e9050ca426a74069b99b63ca4a6520b05c2f8c7`. The systemd service is
`rfd4-annotation-studio.service`, listens on port `8000`, permits one active
design job, and stores sessions, jobs, generated CIFs, metrics, and compilation
caches in the persistent state directory.

AF3 is intentionally disabled for this one-GPU pilot. Enable it only on a
separately accepted two-GPU deployment.

## Acceptance record

The 2026-07-25 release gate verified:

- unauthenticated HTTPS requests are redirected to Brev login;
- both Patinae and Mol* browser smoke suites pass through the Brev tunnel;
- annotated CIF upload, selection, editing, download, and reopen pass;
- a real one-step RFD4 job completes and produces one CIF output;
- the generated output opens as a new Studio session;
- job progress reports startup, compilation, model loading, sampling, output
  collection, and completion;
- the service survives a systemd restart and reloads its persistent session;
- a warm one-step acceptance job takes approximately 52 seconds on the L40S.

The instance-local acceptance artifact is:

```text
/home/ubuntu/workspace/atomworks-studio/artifacts/brev-20260725T204141Z/result.json
```

## Operations

```bash
# Health and logs
brev exec rfd4-studio-cloud \
  "curl -fsS http://127.0.0.1:8000/api/health"
brev exec rfd4-studio-cloud \
  "sudo systemctl status rfd4-annotation-studio.service --no-pager"

# Private operator access
brev port-forward rfd4-studio-cloud --port 18000:8000

# Preserve disk, image, sessions, jobs, and compilation caches
brev stop rfd4-studio-cloud
brev start rfd4-studio-cloud
```

After every start, verify `/api/health`, `/api/ready`, the authenticated HTTPS
URL, and one output-open workflow before inviting additional users.
