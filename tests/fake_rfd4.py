"""Tiny command-compatible RFD4 stand-in used only by Annotation Studio tests."""

from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

import yaml

arguments = dict(zip(sys.argv[1::2], sys.argv[2::2], strict=True))
config = yaml.safe_load((Path(arguments["--config-path"]) / "config.yaml").read_text(encoding="utf-8"))
output = Path(config["out_dir"])
example_id = "abc123__design_rep0"
sample = output / "inference_samples" / "ab" / "abc12345" / f"{example_id}_generated.cif"
sample.parent.mkdir(parents=True)
shutil.copyfile(config["inputs"], sample)
metrics = output / "inference_metrics"
metrics.mkdir(parents=True)
with (metrics / "per_example_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["example_id", "search.reward", "oracle.plddt_coi"])
    writer.writeheader()
    writer.writerow({"example_id": example_id, "search.reward": 8.5, "oracle.plddt_coi": 91.2})
