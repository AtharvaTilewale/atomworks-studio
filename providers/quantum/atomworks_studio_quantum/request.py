"""The browser's quantum job request: an aq job request (``request.json``) without paths or backend options."""

from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JOB_SCHEMA = "atomworks-quantum.job/1"
MAX_STAGES = 16
# RFD4 caps its uploaded options at 128 KiB; aq stage objects are far smaller.
MAX_REQUEST_BYTES = 64 * 1024


class CalculatorChoice(BaseModel):
    """A catalog model and where to run it.

    Backend options are refused: some of them (a Gaussian checkpoint, an ORCA input block) name files
    on the machine that runs the job.
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    device: str | None = None
    dtype: str | None = None


# "<model>[@<device>]" or a choice object; aq validates the model name and the device.
Calculator = str | CalculatorChoice


def _refuse_options(value: object) -> object:
    if isinstance(value, dict) and "options" in value:
        raise ValueError("calculator options are not accepted from Studio; choose a model, device and dtype only")
    return value


class QuantumStage(BaseModel):
    """One aq stage object; aq validates its task and task-specific keys."""

    model_config = ConfigDict(extra="allow")

    calculator: Calculator | None = None

    _calculator = field_validator("calculator", mode="before")(_refuse_options)


class QuantumJobRequest(BaseModel):
    """A quantum job: the state of the input structure, a default calculator, and up to 16 stages."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["atomworks-quantum.job/1"] = Field(default=JOB_SCHEMA, alias="schema")
    charge: int | Literal["formal"]
    multiplicity: int = Field(ge=1)
    calculator: Calculator | None = None
    stages: list[QuantumStage] = Field(min_length=1, max_length=MAX_STAGES)

    _calculator = field_validator("calculator", mode="before")(_refuse_options)

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if len(json.dumps(self.job())) > MAX_REQUEST_BYTES:
            raise ValueError(f"the request exceeds {MAX_REQUEST_BYTES // 1024} KiB")
        return self

    def job(self) -> dict[str, Any]:
        """``request.json`` for ``aq job``: the fields the browser sent, with the job format tag."""

        return {"schema": JOB_SCHEMA, **self.model_dump(mode="json", exclude={"format"}, exclude_unset=True)}

    def models(self) -> set[str]:
        """The catalog names of every calculator in the request."""

        calculators = (self.calculator, *(stage.calculator for stage in self.stages))
        return {
            calculator.model if isinstance(calculator, CalculatorChoice) else calculator.partition("@")[0]
            for calculator in calculators
            if calculator is not None
        }
