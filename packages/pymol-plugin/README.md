# AtomWorks Studio for PyMOL

## Overview: A New Way to Design Proteins

AtomWorks Studio for PyMOL replaces the traditional JSON/YAML contig-based
protein design workflow with a visual one. Instead of writing config files to
describe what you want a model to do, you build the design task directly in
PyMOL: load a real structure, manipulate it, annotate exactly what the model
should do, and save. The result is an **annotated CIF file** that models such as
RFD4-Proteina read natively—no intermediate JSON or YAML is written or
converted.

**Annotated CIF files** are standard mmCIF files extended with AtomWorks condition blocks. All design intent is stored per-atom and per-residue directly in the file: sequence conditioning, coordinate conditioning, distance restraints, hydrogen bond conditioning, unindexing, and more. This is the single input format that goes directly into the model.

**The workflow shift is significant.** Previously, you described a design task abstractly — chain lengths, contig strings, residue ranges — in a text config. Now, you visualize exactly what you want to design in 3D, annotate it interactively, and hand the model the CIF. What you see in PyMOL is what goes into the model.

---

## The Ground Truth AtomArray: Why It Matters

The plugin maintains an `AtomArrayPlus` in memory as the **authoritative representation** of each loaded structure. This is what gets saved and what the model reads. PyMOL is purely a viewer.

Operations performed via the AtomWorks panel — loading, deleting atoms, inserting expandable segments, editing annotations — are applied to the ground truth array and reflected in PyMOL. Operations performed directly in PyMOL — GUI mutations, coordinate dragging, PyMOL's own selection-based edits — are **not necessarily** reflected in the array and may be silently lost on the next reload.

**If you are unsure whether a change was captured**, press **Reload Visualization**. This re-renders the PyMOL view from the ground truth array. What you see after a reload is exactly what the model will receive.

**General rule: only use AtomWorks plugin methods to load, modify, annotate, and save structures.** Anything done outside the plugin may not be captured.

### Round-Tripping for Unsupported Edits

For advanced PyMOL operations that are not yet supported natively — mutations, coordinate manipulation, rotamer changes — use this workflow:

1. Make the changes in PyMOL using its built-in tools
2. Save the result as a CIF using PyMOL's native File → Save Molecule
3. Reload that CIF using AtomWorks **Load CIF**
4. Re-apply any annotations needed

This will be streamlined in a future version with native support for more PyMOL edit operations.

---

## Setup

For RFD4 authoring, run this once from your local Studio checkout (with Git and
Conda/Miniforge installed):

```bash
make rfd4
```

This creates the dedicated `atomworks-studio-rfd4` environment, initializes and
checks the pinned AtomWorks/RFD4 sources, installs the PyMOL editor and CPU
validation dependencies, and checks that RFD4 accepts a valid structure and
rejects an invalid one. It then registers the plugin and enables the RFD4 editors.
No model weights, inference service, website, or environment-variable setup is needed.
The dependency clones use the same SSH or HTTPS transport as your Studio clone.
If a previous setup failed while cloning, rerun `make rfd4`; it refreshes cached
dependency URLs before retrying.

### Start PyMOL

```bash
conda activate atomworks-studio-rfd4
pymol
```

Choose **Plugin → AtomWorks Studio**. Use **Load CIF**, annotate through the panel, then
**Save Active As**. RFD4 validation runs automatically on save in the same
environment. You do not need to paste Python into the PyMOL console.

The browser and PyMOL editors share the document operations for group actions,
chain centroids, pair restraints, and condition edits, as well as the load presets
and parser options. Both keep scientific storage and validation in the same
Python editing layer; renderer code only presents the result.

### Migrating an existing installation

`make rfd4` creates a separate environment; it does not update your old environment.
It replaces the startup line written by the old `atomworks setup pymol` command,
retains unrelated PyMOL settings, and backs up an existing `~/.pymolrc` to
`~/.pymolrc.before-atomworks-studio`. The guarded startup hook only loads Studio
when Studio is installed in the active PyMOL environment. Existing extension
registrations are migrated, including the old RFD4 editor module name.

You can rerun `make rfd4` to update the Studio packages and native dependencies
in this environment. The environment uses the pthreads build of OpenBLAS so
NumPy/SciPy and PyTorch can load together on macOS without conflicting OpenMP
runtimes. If setup reports `OMP: Error #15`, rerun `make rfd4` from an updated
checkout; no `KMP_DUPLICATE_LIB_OK` override is needed.
The checkout must stay in place because the packages are installed in editable mode.
A changed dependency submodule is rejected rather than silently replaced.

For an existing environment where you have already installed the Studio packages,
register the menu from your **terminal** with:

```bash
python -m atomworks_studio_pymol install
```

### Model-neutral installation

The base plugin can also be installed without RFD4:

```bash
git submodule update --init external/atomworks-dev
conda env create -f packages/pymol-plugin/pymol_env.yaml
conda activate atomworks
python -m pip install -e external/atomworks-dev -e packages/editing -e packages/pymol-plugin
python -m atomworks_studio_pymol install
pymol
```

The plugin runs locally inside PyMOL and uses the shared Studio editing library.
RFD4-specific editors live in Studio; scientific conditions and full pre-save
validation come from the pinned RFD4 source. The CPU validation dependencies are
installed by `make rfd4`; this environment is for authoring, not model inference.

Advanced configurations can still set `ANNOTATION_STUDIO_RFD4_ROOT` or
`ANNOTATION_STUDIO_RFD4_VALIDATION_PYTHON`, but the standard installation needs
neither. When `CLUSTER` selects an existing `.ipd/.env.<cluster>` file in the
RFD4 checkout, validation uses that configuration. If `CLUSTER` is unset or its
file is missing, the validation worker uses an empty explicit dotenv and imports
the same scientific pipeline normally. Inherited settings such as `CLUSTER=local`
therefore do not require a cluster configuration for desktop setup or saving.
The worker leaves existing environment variables unchanged.

### AtomWorks-Quantum results

Install `providers/quantum[pymol]` and register `atomworks_studio_quantum.pymol`
from **Main -> Extensions...** to get `aq_load_result`, which opens
atomworks-quantum result directories; see `providers/quantum/README.md`.

### Open the Panel

Start PyMOL with the conda environment active, then open the panel via:

- **Menu**: Plugin → AtomWorks Studio
- **Console**: `aw_load` (also loads a file directly)

The panel has three tabs: **Main**, **Annotations**, and **Bonds**.

---

## Basic Workflow

### Loading a Structure

Always use **Load CIF** in the Main tab rather than PyMOL's File → Open. This registers the structure with the plugin and builds the ground truth array.

Parse options available in the load dialog:

| Option | Default | Description |
|---|---|---|
| Load standard annotations | On | Load AtomWorks annotations saved in the CIF (design CIFs) |
| Add missing atoms | Off | Rebuild missing sidechain/hydrogen atoms from the CCD |
| Remove waters | On | Strip water molecules on load |

The plugin always loads the first biological assembly when one is present.

### Saving a Structure

Always use **Save Active As** in the Main tab. This writes a full annotated CIF with all conditions embedded — the file is ready to pass directly to the model.

Do not use PyMOL's native save for structures you intend to use as model inputs; it does not write AtomWorks annotations.

### Selecting the Active Object

The **Active** dropdown at the top of the Main tab shows which object all panel operations apply to. If multiple structures are loaded, use this to switch between them. **When adding annotations, make sure the active object is the one you want to add annotations to!** You can also switch from the console:

```
aw_set_active my_object
```

### Deleting Atoms

Make a selection in the PyMOL viewport, then click **Delete selection** in the Main tab. This removes the selected atoms from the ground truth array and reloads the view.

### Combining Structures

**Combine visible** merges all currently visible AtomWorks-managed objects into a single structure under a new name. Chain conflicts are resolved automatically. Source objects are hidden after combining.

This is useful for assembling a complex from separately loaded chains before annotating.

### Undo

The **Undo** button reverts the last edit to the active object. Up to 10 undo levels are maintained per object. Note these undo actions are only for AtomWorks operations (anything done in the AtomWorks Studio panels).

---

## Expandable Segments

Expandable segments represent **variable-length designed segments** — the visual equivalent of writing `"10-15"` in an RFD3 contig string. Rather than specifying a length range in a config file, you insert an expandable segment directly into the structure. The model samples a concrete length at runtime.

This means your input CIF can contain genuine structural uncertainty: a binder scaffold with an expandable segment between two helices, for example, tells the model that it should design a linker of some sampled length rather than a fixed sequence.

### Expandable Segment Operations

| Button | Description |
|---|---|
| **Insert Expandable Segment...** | Insert a segment before or after the current selection, or as a new standalone chain |
| **Realize Segments** | Replace segment sentinels with actual placeholder residues (for downstream processing) |
| **Collapse Segments** | Merge consecutive segment residues back into a single sentinel |

When inserting an expandable segment, you specify:
- **Min / Max length**: the range the model will sample from (e.g. min 10, max 15 → `"10-15"`)
- **Insert before / after**: relative to the current selection
- **New chain**: insert as an independent chain rather than within an existing one

**Note on realizing segments:** If you save an annotated CIF with segments realized, the models will NOT sample at run time as you have effectively already sampled from your ranges. Save annotated CIFs with segments collapsed if you want the model to sample from the ranges at run time.

---

## Annotations

The **Annotations** tab is where design intent is specified. Each annotation corresponds to a condition that the model reads from the CIF. Select an annotation type from the list on the left; the editor for that annotation appears on the right. Changes are written immediately to the ground truth array and saved into the CIF when you use Save Active As.

The plugin owns its editor registrations. It does not load PyMOL editor code
from AtomWorks, RFD4, or other model packages.

**To mark residues as unindexed, simply toggle off the Index condition (everything defaults to indexed). That's it!**

Once you have conditions applied to your atom array, you will see PyMOL selections for each condition. Besides certain visual cues (distance conditions visualized as PyMOL distances, etc.) these selections indicate which atoms have which conditions. The names of the conditions generally have the format:

`condition_{condition_name}_{n_body}_{level}_{object_name}`

For example, the index condition has a selection called: `mask_index_1_residue_6lyz` for the object `6lyz`.

---

## Bonds

The **Bonds** tab allows manual editing of the bond graph for cases where automatic inference is insufficient — non-standard linkages, metal coordination, unusual ligands.

**WARNING**: `.pdb` files do not necessarily maintain all the bonds you think they do! Always double check which bonds are actually encoded and add/remove bonds as necessary! This is especially important for complicated bond graphs such as ligands, non-canonicals, post-translational modifications, etc.

- **Pick atoms**: click Select Atom 1 / Select Atom 2, then click atoms in the PyMOL viewport
- **Add / Remove bond**: apply or remove a bond of the chosen type between the two picked atoms
- **Bonds table**: shows all bonds involving the current PyMOL selection; individual bonds can be deleted from here
- **Infer Bonds from CCD**: replaces all bonds using CCD template matching (requires `CCD_MIRROR_PATH` to be set)

---

## Console Commands

All panel operations are also available as `aw_*` console commands for scripting:

| Command | Description |
|---|---|
| `aw_load <path> [obj_name]` | Load a CIF into AtomWorks |
| `aw_save <path> [obj_name]` | Save the active (or named) object as annotated CIF |
| `aw_delete [selection] [obj_name]` | Delete selection from the active object |
| `aw_insert_segment [selection] [min] [max] [before]` | Insert an expandable segment at the selection |
| `aw_undo [obj_name]` | Undo the last edit |
| `aw_combine_visible [new_name]` | Combine all visible tracked objects |
| `aw_set_active <obj_name>` | Set the active object |

---

## Updating AtomWorks Studio

From the repository root:

```bash
git pull --ff-only
git submodule update --init
conda activate atomworks
python -m pip install \
  -e external/atomworks-dev \
  -e packages/editing \
  -e packages/pymol-plugin
```

Then restart PyMOL.

## Limitations and Future Work

- **Coordinate editing, mutations, and rotamer changes** made directly in PyMOL are not yet natively captured by the plugin. Use the round-trip workflow described above (edit in PyMOL → save CIF → reload with Load CIF) until native support is added.
- **Only CIF files** are fully supported for load/save. PDB files can be viewed in PyMOL alongside AtomWorks objects but will not be registered with the plugin.
- Tighter PyMOL integration — including automatic capture of PyMOL-side edits — is planned for a future release.

## Feature Requests and Bug Reports

AtomWorks Studio is in beta. Report bugs and request features in the
[AtomWorks Studio issue tracker](https://github.com/baker-laboratory/atomworks-studio-dev/issues).
