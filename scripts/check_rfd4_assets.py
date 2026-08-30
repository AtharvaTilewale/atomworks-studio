"""Verify the RFD4 inference assets selected by its pinned configuration."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_BUCKET_URL = "https://rfd4-proteina-public-e04a.cwobject.com"
DATA_PATH_TOKEN = "${oc.env:DATA_PATH}"


@dataclass(frozen=True)
class Asset:
    relative_path: Path
    local_path: Path
    url: str


@dataclass(frozen=True)
class Arguments:
    rfd4_root: Path
    data_path: Path | None
    check_bucket: bool
    bucket_only: bool
    report_path: Path | None


def _yaml_scalar(document: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*([^#\n]+?)\s*$", document)
    if match is None:
        raise ValueError(f"Missing {key!r} in configs/inference.yaml")
    return match.group(1).strip().strip("\"'")


def _data_path_value(value: str, data_path: Path) -> Path:
    if value == DATA_PATH_TOKEN:
        return data_path
    prefix = f"{DATA_PATH_TOKEN}/"
    if not value.startswith(prefix):
        raise ValueError(f"Expected an RFD4 DATA_PATH reference, got {value!r}")
    return data_path / value.removeprefix(prefix)


def required_assets(rfd4_root: Path, data_path: Path, bucket_url: str) -> tuple[Asset, ...]:
    config_path = rfd4_root / "configs/inference.yaml"
    document = config_path.read_text(encoding="utf-8")
    checkpoint_name = _yaml_scalar(document, "ckpt_name")
    if Path(checkpoint_name).name != checkpoint_name:
        raise ValueError(f"RFD4 ckpt_name must be a filename, got {checkpoint_name!r}")

    paths = (
        _data_path_value(_yaml_scalar(document, "ckpt_path"), data_path) / checkpoint_name,
        _data_path_value(_yaml_scalar(document, "autoencoder_ckpt_path"), data_path),
    )
    data_root = data_path.resolve()
    assets: list[Asset] = []
    for local_path in paths:
        resolved = local_path.resolve()
        try:
            relative_path = resolved.relative_to(data_root)
        except ValueError as error:
            raise ValueError(f"RFD4 asset escapes DATA_PATH: {local_path}") from error
        assets.append(
            Asset(
                relative_path=relative_path,
                local_path=local_path,
                url=f"{bucket_url.rstrip('/')}/{quote(relative_path.as_posix())}",
            )
        )
    return tuple(assets)


def local_size(asset: Asset) -> int:
    try:
        size = asset.local_path.stat().st_size
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Missing {asset.local_path}; run the pinned RFD4 scripts/download_example_data.sh"
        ) from error
    if size <= 0:
        raise ValueError(f"RFD4 asset is empty: {asset.local_path}")
    return size


def remote_size(asset: Asset, timeout: float = 30) -> int:
    try:
        with urlopen(Request(asset.url, method="HEAD"), timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            size = int(content_length) if content_length else 0
    except (OSError, ValueError) as error:
        raise ConnectionError(f"Could not read public RFD4 asset: {asset.url}") from error
    if size <= 0:
        raise ValueError(f"Public RFD4 asset has no content: {asset.url}")
    return size


def parse_args() -> Arguments:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rfd4-root", type=Path, required=True)
    parser.add_argument("--data-path", type=Path)
    parser.add_argument(
        "--check-bucket",
        action="store_true",
        help="also verify anonymous availability and byte size in the public bucket",
    )
    parser.add_argument(
        "--bucket-only",
        action="store_true",
        help="verify the public bucket without requiring downloaded local assets",
    )
    parser.add_argument("--json", dest="report_path", type=Path, help="write the verification result as JSON")
    namespace = parser.parse_args()
    configured_data_path = os.environ.get("DATA_PATH")
    return Arguments(
        rfd4_root=namespace.rfd4_root,
        data_path=namespace.data_path or (Path(configured_data_path) if configured_data_path else None),
        check_bucket=namespace.check_bucket,
        bucket_only=namespace.bucket_only,
        report_path=namespace.report_path,
    )


def main() -> int:
    args = parse_args()
    if args.data_path is None and not args.bucket_only:
        print("RFD4 asset check failed: set DATA_PATH or pass --data-path", file=sys.stderr)
        return 2
    data_path = args.data_path or Path("/")
    bucket_url = os.environ.get("RFPROTEINA_DATA_URL", DEFAULT_BUCKET_URL)
    try:
        assets = required_assets(args.rfd4_root, data_path, bucket_url)
        results: list[dict[str, str | int | None]] = []
        for asset in assets:
            local_bytes = None if args.bucket_only else local_size(asset)
            bucket_bytes = remote_size(asset) if args.check_bucket or args.bucket_only else None
            if local_bytes is not None and bucket_bytes is not None and local_bytes != bucket_bytes:
                raise ValueError(
                    f"Local and public sizes differ for {asset.relative_path}: {local_bytes} != {bucket_bytes}"
                )
            results.append(
                {
                    "path": asset.relative_path.as_posix(),
                    "local_bytes": local_bytes,
                    "bucket_bytes": bucket_bytes,
                    "url": asset.url,
                }
            )
    except (ConnectionError, OSError, ValueError) as error:
        print(f"RFD4 asset check failed: {error}", file=sys.stderr)
        return 1

    report = {"status": "ok", "bucket_url": bucket_url, "assets": results}
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
