"""Compare native/browser/VS Code workflow exports as scientific data, not CIF timestamps."""

import argparse
import json
from pathlib import Path

import numpy as np
from atomworks_studio_editing.document import StudioDocument
from atomworks_studio_editing.providers import load_providers


def document(path):
    providers = load_providers()
    return StudioDocument.from_cif(
        path.read_text(),
        condition_provider=providers.condition_provider,
        validation_provider=providers.validation_provider,
    )


def compare(left, right):
    a, b = left.array, right.array
    assert len(a) == len(b), (len(a), len(b))
    for field in ("chain_id", "res_id", "ins_code", "res_name", "atom_name", "element", "hetero", "charge"):
        np.testing.assert_equal(a.get_annotation(field), b.get_annotation(field), err_msg=field)
    np.testing.assert_allclose(a.coord, b.coord, atol=0.001, rtol=0, equal_nan=True)
    for field in ("annotation_expsegmin_1_residue", "annotation_expsegmax_1_residue"):
        np.testing.assert_equal(a.get_annotation(field), b.get_annotation(field), err_msg=field)
    bonds_a = sorted(tuple(row) for row in a.bonds.as_array()) if a.bonds is not None else []
    bonds_b = sorted(tuple(row) for row in b.bonds.as_array()) if b.bonds is not None else []
    assert bonds_a == bonds_b, "stored bonds differ"
    for definition in left.condition_provider.definitions:
        cls = definition.condition
        x, y = cls.annotation(a, default="generate"), cls.annotation(b, default="generate")
        if definition.kind == "pair":
            np.testing.assert_equal(x.pairs, y.pairs, err_msg=definition.key)
            np.testing.assert_allclose(x.values, y.values, atol=0.001, rtol=0, equal_nan=True)
        elif np.asarray(x).dtype.kind in "fc":
            np.testing.assert_allclose(x, y, atol=0.001, rtol=0, equal_nan=True, err_msg=definition.key)
        else:
            np.testing.assert_equal(x, y, err_msg=definition.key)
        mx, my = cls.mask(a, default="generate"), cls.mask(b, default="generate")
        if definition.kind == "pair":
            np.testing.assert_equal(mx.pairs, my.pairs, err_msg=definition.key)
            np.testing.assert_equal(mx.values, my.values, err_msg=definition.key)
        else:
            np.testing.assert_equal(mx, my, err_msg=definition.key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--hosts", nargs="+", default=["molstar", "patinae", "vscode-molstar", "vscode-patinae"])
    parser.add_argument("--reference", default="pymol")
    args = parser.parse_args()
    cases = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/workflow_cases.json").read_text())
    checks = 0
    for case in cases:
        stages = [
            "00",
            "saved",
            *(f"{i:02d}" for i, action in enumerate(case["actions"], 1) if action["kind"] != "realize"),
        ]
        for stage in stages:
            native = document(args.directory / f"{args.reference}-{case['name']}-{stage}.cif")
            for host in args.hosts:
                actual = document(args.directory / f"{host}-{case['name']}-{stage}.cif")
                try:
                    compare(native, actual)
                except AssertionError as error:
                    raise AssertionError(f"{host} / {case['name']} / {stage}: {error}") from error
                checks += 1
        print(f"{case['name']}: {args.reference} and {', '.join(args.hosts)} outputs agree")
    print(
        f"{checks} cross-interface comparisons: atom identity, coordinates, bonds, charges, "
        "segment bounds, all condition values/masks"
    )


if __name__ == "__main__":
    main()
