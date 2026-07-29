"""Optional RFD4-specific PyMOL condition editors for AtomWorks Studio.

Migrated from RFD4-Proteina ``rfproteina.pymol`` (aligns with RFD4-Proteina-dev PR #107)
so all PyMOL code lives in atomworks-studio. These editors extend the model-neutral
``atomworks_studio_pymol`` plugin with RFD4 design conditions (index groups, chain
centroids, internal coordinates). They import the full ``rfproteina`` model runtime, so
they are an OPTIONAL extra used only in the GPU/Brev setup -- the base plugin and the CIF
editor work without them. Install via ``atomworks-annotation-studio-rfd4[pymol]`` with a
PYTHONPATH that includes the RFD4 checkout.
"""
