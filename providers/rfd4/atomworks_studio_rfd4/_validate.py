"""Standalone worker: initialize the real model runtime only in this process."""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import json
import os
import pickle
import sys
from pathlib import Path


def configure_local_runtime(source: Path) -> None:
    """Use the selected cluster dotenv when present, otherwise desktop defaults.

    CPU-only validation does not require cluster configuration. An inherited
    CLUSTER value with no matching file uses an empty explicit dotenv, without
    changing environment variables or bypassing the scientific pipeline.
    """
    if "rfproteina" in sys.modules:
        return
    name = "rfproteina.utils.env"
    spec = importlib.util.spec_from_file_location(name, source / "rfproteina/utils/env.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load RFD4 environment configuration")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cluster = os.environ.get("CLUSTER")
    if not cluster or not (module.DOTENV_ROOT_PATH / f".env.{cluster}").is_file():
        module.load_dotenv = functools.partial(module.load_dotenv, dotenv_path=os.devnull)
    sys.modules[name] = module


def main() -> None:
    sys.path.insert(0, sys.argv[1])
    messages = []
    try:
        with contextlib.redirect_stdout(sys.stderr):
            configure_local_runtime(Path(sys.argv[1]))
            import rfproteina  # noqa: F401
            from rfproteina.conditions_reporting import (
                check_index_group_molecule_consistency,
                check_index_group_res_id_gaps,
                run_pipeline_validation,
            )

            # Only Studio's parent process supplies this private pipe. Never
            # expose it as a file/upload format. CIF reparsing would repair or
            # round invalid structures before validating them and lose precision.
            array = pickle.load(sys.stdin.buffer)
            if "--pre-save" in sys.argv[2:]:
                # AtomWorks' panel isolates every extension hook, preserving
                # later findings even when an earlier check cannot run.
                for hook in (
                    run_pipeline_validation,
                    check_index_group_res_id_gaps,
                    check_index_group_molecule_consistency,
                ):
                    try:
                        messages.extend(hook(array.copy()))
                    except Exception as error:
                        messages.append(f"[hook_error] {error}")
            else:
                messages.extend(run_pipeline_validation(array))
    except Exception as error:
        messages.append(f"[pipeline_error] {error}")
    print(json.dumps(messages))


if __name__ == "__main__":
    main()
