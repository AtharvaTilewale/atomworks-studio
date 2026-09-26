"""AtomWorks-Quantum commands for the AtomWorks Studio PyMOL plugin.

Install ``atomworks-annotation-studio-quantum[pymol]`` into PyMOL's environment and register the module path
``atomworks_studio_quantum.pymol`` from the plugin's **Main -> Extensions...** dialog. This adds:

- ``aq_load_result directory [, name]``: loads an aq result directory (written by ``Result.save``, or a stage of
  ``aq run`` or ``aq job run``). Its structure becomes a tracked, editable object; its trajectories become
  multi-state objects; its result summary is printed.
- a **Summarize changes** section with the total charge and multiplicity of an object loaded that way.

atomworks-quantum is not needed in PyMOL's environment: the command reads the files aq wrote. PyMOL is imported
only when :func:`register` runs, but the parent package imports the Studio API (``annotation_studio`` from
``packages/api``, a dependency of this distribution), which loads AtomWorks.
"""


def register() -> None:
    """Add the ``aq_load_result`` command and the electronic-state summary hook to the plugin."""
    from pymol import cmd

    from atomworks_studio_quantum.pymol.commands import aq_load_result, summarize_electronic_state

    cmd.extend("aq_load_result", aq_load_result)
    try:
        from atomworks_studio_pymol.panel import register_summary_hook
    except ImportError:
        return  # No Qt: the command works, and there is no Summarize changes dialog to extend.
    register_summary_hook(summarize_electronic_state)
