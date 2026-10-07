import discoCif from "../../../examples/disco_iai.cif?raw";
import mcsaCif from "../../../examples/mcsa_ph2.cif?raw";
import ppiCif from "../../../examples/ppi_pdl1.cif?raw";

export type StudioExample = { id: string; name: string; filename: string; cif: string };

export const STUDIO_EXAMPLES: StudioExample[] = [
  { id: "ppi", name: "PPI · PD-L1 binder", filename: "ppi_pdl1.cif", cif: ppiCif },
  { id: "mcsa", name: "MCSA · PH2 active site", filename: "mcsa_ph2.cif", cif: mcsaCif },
  { id: "disco", name: "DISCO · IAI ligand binder", filename: "disco_iai.cif", cif: discoCif },
];
