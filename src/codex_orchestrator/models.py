"""The plan contract shared by local templates and Codex output."""

from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Workstream(StrEnum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"
    INTEGRATION = "integration"


class PlanningMode(StrEnum):
    TEMPLATE = "template"
    CODEX = "codex"


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Text
    title: Text
    workstream: Workstream
    description: Text
    depends_on: list[Text]
    acceptance_criteria: Annotated[list[Text], Field(min_length=1)]


class PlanContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assumptions: list[Text]
    tasks: Annotated[list[Task], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        ids = {task.id for task in self.tasks}
        if len(ids) != len(self.tasks):
            raise ValueError("Task IDs must be unique")
        for task in self.tasks:
            if task.id in task.depends_on:
                raise ValueError(f"Task {task.id} cannot depend on itself")
            if len(set(task.depends_on)) != len(task.depends_on):
                raise ValueError(f"Task {task.id} has duplicate dependencies")
            unknown = set(task.depends_on) - ids
            if unknown:
                raise ValueError(f"Task {task.id} has unknown dependencies: {sorted(unknown)}")
        try:
            TopologicalSorter({task.id: task.depends_on for task in self.tasks}).prepare()
        except CycleError as exc:
            raise ValueError("Task dependencies contain a cycle") from exc
        return self


class ProjectPlan(PlanContent):
    schema_version: Literal[1] = 1
    project_name: Text
    brief: Text
    planning_mode: PlanningMode
