"""Load every installed Studio provider without making any an API import dependency.

Provider distributions register a module under the ``atomworks_studio.providers`` entry-point
group. A module may export document providers (CONDITION_PROVIDER, optionally
VALIDATION_PROVIDER and CONDITION_OPERATIONS), a job provider factory
``create_job_provider(project_root) -> JobProvider | None``, and a display PROVIDER_LABEL.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel

from .contracts import (
    NEUTRAL_CONDITION_PROVIDER,
    NEUTRAL_VALIDATION_PROVIDER,
    ConditionOperation,
    ConditionProvider,
    JobProvider,
    ValidationProvider,
)

ENTRY_POINT_GROUP = "atomworks_studio.providers"
LOGGER = logging.getLogger("atomworks_studio")
# Provider failures that leave the API running without that provider.
TOLERATED_ERRORS = (ImportError, OSError, RuntimeError)


class ProviderSummary(BaseModel):
    """One installed provider as the browser sees it."""

    name: str
    label: str
    # Jobs can run on this deployment; always true for providers without jobs.
    enabled: bool
    # This provider supplies the session condition editor and validation.
    conditions: bool
    jobs: bool


@dataclass(slots=True)
class ProviderRegistry:
    """The document provider and every named job provider of this API process."""

    condition_provider: ConditionProvider = NEUTRAL_CONDITION_PROVIDER
    validation_provider: ValidationProvider = NEUTRAL_VALIDATION_PROVIDER
    condition_operations: Mapping[str, ConditionOperation] = field(default_factory=dict)
    document_provider: str | None = None
    installed: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    job_providers: dict[str, JobProvider] = field(default_factory=dict)

    def add_job_provider(self, name: str, provider: JobProvider, *, label: str) -> None:
        """Register a job provider; an installed module's PROVIDER_LABEL takes precedence over label."""

        if name in self.job_providers:
            raise RuntimeError(f"more than one Studio job provider is named {name!r}")
        self.job_providers[name] = provider
        self.labels.setdefault(name, label)

    def summaries(self) -> list[ProviderSummary]:
        return [
            ProviderSummary(
                name=name,
                label=self.labels.get(name, name),
                enabled=self.job_providers[name].enabled if name in self.job_providers else True,
                conditions=name == self.document_provider,
                jobs=name in self.job_providers,
            )
            for name in sorted({*self.installed, *self.job_providers})
        ]

    def close(self) -> None:
        for provider in self.job_providers.values():
            provider.close()


def load_providers(project_root: Path) -> ProviderRegistry:
    """Load all provider entry points, sorted by name, and choose the document provider.

    ANNOTATION_STUDIO_CONDITION_PROVIDER names the document provider; otherwise the first
    module that exports CONDITION_PROVIDER supplies it, or the neutral providers do. Import
    and job-factory errors skip that part of a provider unless
    ANNOTATION_STUDIO_PROVIDER_REQUIRED=1.
    """

    registry = ProviderRegistry()
    modules: dict[str, ModuleType] = {}
    failures: dict[str, str] = {}
    for entry_point in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda item: item.name):
        name = entry_point.name
        try:
            module = entry_point.load()
        except TOLERATED_ERRORS as error:
            failures[name] = str(error)
            continue
        modules[name] = module
        registry.installed.append(name)
        if label := getattr(module, "PROVIDER_LABEL", None):
            registry.labels[name] = label
        if (factory := getattr(module, "create_job_provider", None)) is None:
            continue
        try:
            job_provider = factory(project_root)
        except TOLERATED_ERRORS as error:
            failures[name] = f"could not create its job provider: {error}"
            continue
        if job_provider is not None:
            registry.job_providers[name] = job_provider
    if failures and os.environ.get("ANNOTATION_STUDIO_PROVIDER_REQUIRED") == "1":
        raise RuntimeError(
            "could not initialize a required Studio provider: "
            + "; ".join(f"{name}: {error}" for name, error in failures.items())
        )
    for name, error in failures.items():
        LOGGER.warning("Studio provider %s is unavailable: %s", name, error)

    document_provider = _document_provider_name(modules, failures)
    if document_provider is not None:
        module = modules[document_provider]
        registry.document_provider = document_provider
        registry.condition_provider = module.CONDITION_PROVIDER
        registry.validation_provider = getattr(module, "VALIDATION_PROVIDER", NEUTRAL_VALIDATION_PROVIDER)
        registry.condition_operations = getattr(module, "CONDITION_OPERATIONS", {})
    return registry


def _document_provider_name(modules: Mapping[str, ModuleType], failures: Mapping[str, str]) -> str | None:
    requested = os.environ.get("ANNOTATION_STUDIO_CONDITION_PROVIDER")
    if not requested:
        return next((name for name, module in modules.items() if hasattr(module, "CONDITION_PROVIDER")), None)
    setting = f"ANNOTATION_STUDIO_CONDITION_PROVIDER={requested}"
    if requested in failures and requested not in modules:
        raise RuntimeError(f"{setting}: the provider failed to load: {failures[requested]}")
    if requested not in modules:
        raise RuntimeError(f"{setting}: no installed provider has that name; installed: {', '.join(modules) or 'none'}")
    if not hasattr(modules[requested], "CONDITION_PROVIDER"):
        raise RuntimeError(f"{setting}: the provider exports no CONDITION_PROVIDER")
    return requested
