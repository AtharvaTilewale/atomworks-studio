import discoCif from "../../examples/disco_iai.cif?raw";
import mcsaCif from "../../examples/mcsa_ph2.cif?raw";
import ppiCif from "../../examples/ppi_pdl1.cif?raw";

export type StudioExample = {
  id: "ppi" | "mcsa" | "disco";
  name: string;
  filename: string;
  cif: string;
  metricsTags: string[];
  search: {
    rewardTag: string;
    rewardMetric: string;
    rewardWeight: number;
  };
};

export const STUDIO_EXAMPLES: StudioExample[] = [
  {
    id: "ppi",
    name: "PPI · PD-L1 binder",
    filename: "ppi_pdl1.cif",
    cif: ppiCif,
    metricsTags: [
      "design",
      "3d_coordinate_conditioning",
      "hotspot_conditioning",
      "interchain_interactions",
      "ppi_refolding",
    ],
    search: {
      rewardTag: "interchain_interactions",
      rewardMetric: "interchain_interactions.hbond_atom_pair_count_coi",
      rewardWeight: 1,
    },
  },
  {
    id: "mcsa",
    name: "MCSA · PH2 active site",
    filename: "mcsa_ph2.cif",
    cif: mcsaCif,
    metricsTags: ["design", "unindexed", "3d_coordinate_conditioning", "ame_refolding"],
    search: {
      rewardTag: "design",
      rewardMetric: "clash_metrics.frac_atoms_clashing_1p5A",
      rewardWeight: -1,
    },
  },
  {
    id: "disco",
    name: "DISCO-style · IAI ligand binder",
    filename: "disco_iai.cif",
    cif: discoCif,
    metricsTags: [
      "design",
      "interchain_interactions",
      "sm_binder_refolding",
      "disco_refolding",
    ],
    search: {
      rewardTag: "interchain_interactions",
      rewardMetric: "interchain_interactions.hbond_atom_pair_count_coi",
      rewardWeight: 1,
    },
  },
];
