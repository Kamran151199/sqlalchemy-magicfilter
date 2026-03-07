"""Explicit query and load specification types for strict magicfilter usage."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from sqlalchemy.orm import InstrumentedAttribute


ValueAdapter: TypeAlias = Callable[[Any], Any]
RelationFilterStrategy: TypeAlias = Literal["auto", "join", "has", "any"]
LoadStrategy: TypeAlias = Literal["joined", "selectin"]


@dataclass(frozen=True)
class ScalarFieldSpec:
    """A directly filterable scalar field."""

    attr: InstrumentedAttribute
    operators: frozenset[str]
    value_adapter: ValueAdapter | None = None

    def adapt(self, value: Any) -> Any:  # noqa: ANN401
        """Apply a value adapter to one value or a sequence of values."""
        if self.value_adapter is None:
            return value
        if isinstance(value, list):
            return [self.value_adapter(item) for item in value]
        return self.value_adapter(value)


@dataclass(frozen=True)
class RelationFieldSpec:
    """A traversable relationship segment in the filter DSL."""

    attr: InstrumentedAttribute
    kind: Literal["one", "many"]
    target_fields: Mapping[str, FieldSpec]
    filter_strategy: RelationFilterStrategy = "auto"

    def resolved_filter_strategy(self) -> Literal["join", "has", "any"]:
        """Resolve the effective strategy for this relation filter."""
        if self.filter_strategy == "auto":
            return "has" if self.kind == "one" else "any"
        return self.filter_strategy


FieldSpec: TypeAlias = ScalarFieldSpec | RelationFieldSpec


@dataclass(frozen=True)
class QuerySpec:
    """Explicit filterable and sortable contract for a root SQLAlchemy model."""

    root_model: type[Any] | None = None
    filter_fields: Mapping[str, FieldSpec] = field(default_factory=dict)
    sort_fields: Mapping[str, InstrumentedAttribute] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadFieldSpec:
    """One eagerly loaded relationship in an explicit load spec."""

    attr: InstrumentedAttribute
    strategy: LoadStrategy = "joined"
    nested: LoadSpec | None = None


@dataclass(frozen=True)
class LoadSpec:
    """Explicit eager-loading contract separate from the query DSL."""

    fields: tuple[LoadFieldSpec, ...] = field(default_factory=tuple)

    def __iter__(self):
        """Iterate over declared load fields."""
        return iter(self.fields)
