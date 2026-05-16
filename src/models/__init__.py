"""Pydantic data-model package for Auto Agent Workflow.

This package was previously trapped by a global ``models/`` rule in ``.gitignore``
and therefore never made it into the repository.  As a result none of the
``src.models.*`` imports across the codebase resolved, which broke recording,
analysis, flow editing and execution at the very first import.

The package is now committed.  Keep the modules here aligned with the persistence
layer in ``src/db/models.py`` and the run-time consumers in ``src/recorder``,
``src/analyzer`` and ``src/executor``.
"""
