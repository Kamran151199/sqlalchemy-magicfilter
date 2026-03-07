from __future__ import annotations

from collections import OrderedDict, abc
from typing import Any, Callable

from sqlalchemy import JSON, Select, UnaryExpression, and_, asc, desc, inspect, select
from sqlalchemy.orm import (
    Query,
    InstrumentedAttribute,
    aliased,
    contains_eager,
    joinedload,
    selectinload,
)
from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.sql import operators

from magicfilter.errors import (
    AttributeResolutionError,
    ConfigurationError,
    MagicFilterError,
    OperatorError,
    SpecificationError,
)
from magicfilter.operator import OPERATORS
from magicfilter.spec import LoadSpec, QuerySpec, RelationFieldSpec, ScalarFieldSpec

RELATION_SPLITTER = "___"
OPERATOR_SPLITTER = "__"
DESC_PREFIX = "-"
JSON_OPERATOR = "json"


class QueryBuilder:
    """Build SQLAlchemy queries from magic-filter dictionaries."""

    def __init__(self, operators_map: dict[str, Callable] | None = None):
        self.operators = operators_map or OPERATORS

    def build(
        self,
        model: Any,
        filters: dict[str | Callable, Any] | None = None,
        sort_attrs: list[str] | None = None,
        schema: dict[InstrumentedAttribute, Any] | None = None,
        *,
        spec: QuerySpec | None = None,
        load_spec: LoadSpec | None = None,
    ) -> Query | Select:
        """Build a SQLAlchemy query using either reflective or explicit-spec mode."""
        filters = filters or {}
        sort_attrs = sort_attrs or []
        if schema is not None and load_spec is not None:
            raise ConfigurationError("Pass either `schema` or `load_spec`, not both.")

        schema = self._normalize_load_schema(schema=schema, load_spec=load_spec)

        if spec is not None:
            return self._build_with_spec(model, filters, sort_attrs, schema, spec)

        return self._build_legacy(model, filters, sort_attrs, schema)

    def _build_legacy(
        self,
        model: Any,
        filters: dict[str | Callable, Any],
        sort_attrs: list[str],
        schema: dict[InstrumentedAttribute, Any] | None,
    ) -> Query | Select:
        query = select(model)
        root_cls = model

        attrs = list(set(self._extract_keys(filters) + [s.lstrip(DESC_PREFIX) for s in sort_attrs]))

        aliases: OrderedDict[str, tuple[AliasedClass, InstrumentedAttribute]] = OrderedDict()
        self._make_aliases_for_tables(root_cls, "", attrs, aliases)

        for table_alias, on_clause in aliases.values():
            query = query.outerjoin(target=table_alias, onclause=on_clause)

        filter_expr = self._make_filters(filters, root_cls, aliases)
        query = query.filter(*filter_expr)

        query = query.order_by(*self._make_orders(sort_attrs, root_cls, aliases))

        if schema:
            query = query.options(*self._eager_expr_from_schema(schema, aliases=aliases))

        return query

    def _build_with_spec(
        self,
        model: Any,
        filters: dict[str | Callable, Any],
        sort_attrs: list[str],
        schema: dict[InstrumentedAttribute, Any] | None,
        spec: QuerySpec,
    ) -> Select:
        if spec.root_model is not None and spec.root_model is not model:
            raise ConfigurationError(
                f"QuerySpec root model `{spec.root_model}` does not match requested model `{model}`.",
            )

        query = select(model)
        aliases: OrderedDict[str, tuple[AliasedClass, InstrumentedAttribute]] = OrderedDict()
        filter_expr = self._make_spec_filters(filters, spec, model, aliases)
        for table_alias, on_clause in aliases.values():
            query = query.outerjoin(target=table_alias, onclause=on_clause)
        query = query.filter(*filter_expr)
        query = query.order_by(*self._make_spec_orders(sort_attrs, spec))

        if schema:
            query = query.options(*self._eager_expr_from_schema(schema, aliases=aliases))

        return query

    def _normalize_load_schema(
        self,
        schema: dict[InstrumentedAttribute, Any] | None,
        load_spec: LoadSpec | None,
    ) -> dict[InstrumentedAttribute, Any] | None:
        """Normalize explicit or legacy eager-loading declarations into one schema shape."""
        if load_spec is None:
            return schema

        normalized: dict[InstrumentedAttribute, Any] = {}
        for field in load_spec:
            if field.strategy not in {"joined", "selectin"}:
                raise ConfigurationError(f"Unsupported load strategy `{field.strategy}`.")
            loader = selectinload if field.strategy == "selectin" else joinedload
            nested_schema = self._normalize_load_schema(None, field.nested)
            normalized[field.attr] = (loader, nested_schema) if nested_schema else loader

        return normalized or None

    def _eager_expr_from_schema(
        self,
        schema: dict[InstrumentedAttribute, Any],
        aliases: dict[str, Any] | None = None,
        parent_path: str = "",
        parent_entity: Any = None,  # noqa: ANN401
    ) -> list[Any]:
        """Build eager-loading options from a nested schema."""
        aliases = aliases or {}
        eager_options = []

        for relationship_attr, nested_schema in schema.items():
            rel_name = relationship_attr.key
            current_path = f"{parent_path}{RELATION_SPLITTER}{rel_name}" if parent_path else rel_name

            if isinstance(nested_schema, tuple):
                join_method, nested_schema = nested_schema
            elif callable(nested_schema):
                join_method, nested_schema = nested_schema, None
            else:
                join_method = joinedload

            if current_path in aliases:
                rel_alias, rel_obj = aliases[current_path]
                rel = rel_obj.of_type(rel_alias)
                nested_expr = contains_eager(rel)

                if nested_schema is not None:
                    nested_expr = nested_expr.options(
                        *self._eager_expr_from_schema(
                            nested_schema,
                            aliases,
                            current_path,
                            parent_entity=rel_alias,
                        ),
                    )

                eager_options.append(nested_expr)
            else:
                if parent_entity and isinstance(parent_entity, AliasedClass):
                    rel = getattr(parent_entity, rel_name)
                else:
                    rel = relationship_attr

                target_cls = inspect(rel).mapper.class_

                if join_method == selectinload:
                    nested_expr = join_method(rel)
                else:
                    nested_expr = join_method(
                        rel.of_type(target_cls) if isinstance(parent_entity, AliasedClass) else rel,
                    )

                if nested_schema is not None:
                    nested_expr = nested_expr.options(
                        *self._eager_expr_from_schema(
                            nested_schema,
                            aliases,
                            current_path,
                            parent_entity=target_cls if isinstance(parent_entity, AliasedClass) else None,
                        ),
                    )

                eager_options.append(nested_expr)

        return eager_options

    def _extract_keys(self, filters: dict[str | Callable, Any] | list[Any]) -> list[str]:
        """Extract all raw filter keys from a nested filter tree."""
        keys = []
        if isinstance(filters, abc.Mapping):
            for key, value in filters.items():
                if callable(key):
                    keys.extend(self._extract_keys(value))
                else:
                    keys.append(key)
        elif isinstance(filters, abc.Sequence):
            for item in filters:
                keys.extend(self._extract_keys(item))
        else:
            raise MagicFilterError(f"Unsupported type ({type(filters)}) in filters: {filters}")
        return keys

    def _make_aliases_for_tables(
        self,
        entity: Any,  # noqa: ANN401
        entity_path: str,
        attrs: list[str],
        aliases: OrderedDict[str, tuple[AliasedClass, InstrumentedAttribute]],
    ) -> None:
        """Create aliases for each referenced relationship path."""
        relations: dict[str, list[str]] = {}
        for attr in attrs:
            if RELATION_SPLITTER in attr:
                relation_name, nested_attr = attr.split(RELATION_SPLITTER, 1)
                relations.setdefault(relation_name, []).append(nested_attr)

        for relation_name, nested_attrs in relations.items():
            path = f"{entity_path}{RELATION_SPLITTER}{relation_name}" if entity_path else relation_name
            relationship = getattr(entity, relation_name)
            alias = aliased(relationship.property.mapper.class_)
            aliases[path] = alias, relationship
            self._make_aliases_for_tables(alias, path, nested_attrs, aliases)

    def _make_filters(
        self,
        filters: dict[str | Callable, Any] | list[Any],
        root_cls: Any,  # noqa: ANN401
        aliases: dict[str, tuple[AliasedClass, InstrumentedAttribute]],
    ) -> list[Any]:
        """Construct SQLAlchemy filter expressions in legacy reflective mode."""
        if isinstance(filters, abc.Mapping):
            expressions = []
            for attr, value in filters.items():
                if callable(attr):
                    nested_expressions = list(self._make_filters(value, root_cls, aliases))
                    expressions.append(attr(*nested_expressions))
                elif RELATION_SPLITTER in attr:
                    parts = attr.rsplit(RELATION_SPLITTER, 1)
                    entity, attr_name = aliases[parts[0]][0], parts[1]
                    expressions.extend(self._make_entity_filters(entity, **{attr_name: value}))
                else:
                    expressions.extend(self._make_entity_filters(root_cls, **{attr: value}))
            return expressions

        if isinstance(filters, abc.Sequence):
            return [expr for item in filters for expr in self._make_filters(item, root_cls, aliases)]

        raise MagicFilterError(f"Unsupported type ({type(filters)}) in filters: {filters}")

    def _make_entity_filters(self, cls_or_alias: AliasedClass | Any, **filters: Any) -> list[Any]:
        """Construct filter expressions for one entity or alias."""
        mapper = cls_or_alias if isinstance(cls_or_alias, AliasedClass) else cls_or_alias
        cls = inspect(cls_or_alias).mapper.class_
        hybrid_methods = getattr(cls, "hybrid_methods", [])
        filterable_attributes = getattr(cls, "filterable_attributes", [])

        expressions = []
        for attr, value in filters.items():
            if attr in hybrid_methods:
                method = getattr(cls, attr)
                expressions.append(method(mapper, value))
                continue

            attr_name, op_name = self._split_expression_key(attr)
            if op_name is not None and op_name not in self.operators and op_name != JSON_OPERATOR:
                raise OperatorError(f"Expression `{attr}` has incorrect operator `{op_name}`")

            column = None
            path: list[str] = []

            if attr_name in filterable_attributes:
                column = getattr(mapper, attr_name)
            elif OPERATOR_SPLITTER in attr_name:
                parts = attr_name.split(OPERATOR_SPLITTER)
                for index in range(len(parts), 0, -1):
                    prefix = OPERATOR_SPLITTER.join(parts[:index])
                    if prefix in filterable_attributes:
                        candidate = getattr(mapper, prefix)
                        if parts[index:] and not isinstance(candidate.type, JSON):
                            raise OperatorError(
                                f"Expression `{attr}` has incorrect operator `{parts[-1]}`",
                            )

                        column = candidate
                        path = parts[index:]
                        break

            if column is None:
                raise AttributeResolutionError(
                    f"Expression `{attr}` has incorrect attribute `{attr_name}`",
                )

            for key in path:
                column = column[key]

            if op_name != JSON_OPERATOR and path:
                column = self._cast_json_expression(column, value)

            if op_name == JSON_OPERATOR:
                expressions.append(self._make_json_expression(column, value))
                continue

            operator_fn = self.operators[op_name] if op_name is not None else operators.eq
            expressions.append(operator_fn(column, value))

        return expressions

    def _make_json_expression(self, column: Any, value: Any) -> Any:  # noqa: ANN401
        """Build a compound JSON path expression from a mapping."""
        if not isinstance(value, dict):
            raise OperatorError(f"Operator `{JSON_OPERATOR}` expects a dictionary, got {type(value)}")

        json_expressions = []
        for json_key, json_value in value.items():
            target_col = column
            for part in json_key.split(OPERATOR_SPLITTER):
                target_col = target_col[part]

            target_col = self._cast_json_expression(target_col, json_value)
            json_expressions.append(target_col == json_value)

        return and_(*json_expressions) if json_expressions else True

    def _make_orders(
        self,
        orders: list[str],
        root_cls: Any,  # noqa: ANN401
        aliases: dict[str, tuple[AliasedClass, InstrumentedAttribute]],
    ) -> list[UnaryExpression]:
        """Construct order expressions in legacy reflective mode."""
        order_expressions = []
        for field in orders:
            prefix = ""
            if field.startswith(DESC_PREFIX):
                prefix, field = DESC_PREFIX, field.lstrip(DESC_PREFIX)

            parts = field.rsplit(RELATION_SPLITTER, 1)
            if RELATION_SPLITTER in field:
                entity, field_name = aliases[parts[0]][0], prefix + parts[1]
            else:
                entity, field_name = root_cls, prefix + field

            order_expressions.extend(self._make_entity_orders(entity, field_name))
        return order_expressions

    @staticmethod
    def _make_entity_orders(cls_or_alias: AliasedClass | Any, *columns: str) -> list[UnaryExpression]:
        """Construct order expressions for one entity or alias."""
        mapper = cls_or_alias if isinstance(cls_or_alias, AliasedClass) else cls_or_alias
        cls = inspect(cls_or_alias).mapper.class_
        expressions = []

        for attr in columns:
            fn, attr = (desc, attr[1:]) if attr.startswith(DESC_PREFIX) else (asc, attr)
            if attr not in cls.sortable_attributes:
                raise AttributeResolutionError(f"Cannot order {cls} by {attr}")
            expressions.append(fn(getattr(mapper, attr)))

        return expressions

    def _make_spec_filters(
        self,
        filters: dict[str | Callable, Any] | list[Any],
        spec: QuerySpec,
        root_entity: Any,  # noqa: ANN401
        aliases: OrderedDict[str, tuple[AliasedClass, InstrumentedAttribute]],
    ) -> list[Any]:
        """Construct SQLAlchemy filter expressions in explicit-spec mode."""
        if isinstance(filters, abc.Mapping):
            expressions = []
            for attr, value in filters.items():
                if callable(attr):
                    nested_expressions = list(self._make_spec_filters(value, spec, root_entity, aliases))
                    expressions.append(attr(*nested_expressions))
                else:
                    expressions.append(self._make_spec_field(spec, root_entity, aliases, attr, value))
            return expressions

        if isinstance(filters, abc.Sequence):
            return [
                expr
                for item in filters
                for expr in self._make_spec_filters(item, spec, root_entity, aliases)
            ]

        raise SpecificationError(f"Unsupported type ({type(filters)}) in filters: {filters}")

    def _make_spec_field(
        self,
        spec: QuerySpec,
        root_entity: Any,  # noqa: ANN401
        aliases: OrderedDict[str, tuple[AliasedClass, InstrumentedAttribute]],
        key: str,
        value: Any,
    ) -> Any:  # noqa: ANN401
        """Construct one SQLAlchemy predicate from a spec-defined filter field."""
        relation_path, field_name, operator_name = self._parse_spec_key(key)
        relation_specs, scalar_spec = self._resolve_spec_field(spec, relation_path, field_name)

        if operator_name not in scalar_spec.operators:
            raise OperatorError(f"Unsupported operator `{operator_name}` for field `{key}`")

        operator_fn = self.operators.get(operator_name)
        if operator_fn is None:
            raise OperatorError(f"Operator `{operator_name}` is not configured")

        resolved_strategies = [
            self._resolve_relation_filter_strategy(relation_spec)
            for _, relation_spec in relation_specs
        ]
        join_prefix_count = 0
        saw_non_join = False
        for strategy in resolved_strategies:
            if strategy == "join":
                if saw_non_join:
                    path = RELATION_SPLITTER.join(relation_path)
                    raise SpecificationError(
                        "Join strategy can only be used on a contiguous relation prefix "
                        f"for `{path}`.",
                    )
                join_prefix_count += 1
            else:
                saw_non_join = True

        current_entity = root_entity
        joined_path: list[str] = []
        for relation_name, relation_spec in relation_specs[:join_prefix_count]:
            joined_path.append(relation_name)
            current_entity = self._ensure_spec_alias(
                current_entity,
                RELATION_SPLITTER.join(joined_path),
                relation_spec,
                aliases,
            )

        scalar_attr = (
            getattr(current_entity, scalar_spec.attr.key)
            if relation_specs and join_prefix_count == len(relation_specs)
            else scalar_spec.attr
        )
        predicate = operator_fn(scalar_attr, scalar_spec.adapt(value))

        remaining_relations = relation_specs[join_prefix_count:]
        for reverse_index, (_, relation_spec) in enumerate(reversed(remaining_relations)):
            source_index = len(relation_specs) - 1 - reverse_index
            strategy = resolved_strategies[source_index]
            relation_attr = relation_spec.attr
            if join_prefix_count and source_index == join_prefix_count:
                relation_attr = getattr(current_entity, relation_spec.attr.key)

            predicate = (
                relation_attr.has(predicate)
                if strategy == "has"
                else relation_attr.any(predicate)
            )

        return predicate

    def _make_spec_orders(self, sort_attrs: list[str], spec: QuerySpec) -> list[UnaryExpression]:
        """Construct order expressions from an explicit query spec."""
        expressions = []
        for sort_key in sort_attrs:
            descending = sort_key.startswith(DESC_PREFIX)
            field_name = sort_key[1:] if descending else sort_key

            try:
                attr = spec.sort_fields[field_name]
            except KeyError:
                raise AttributeResolutionError(f"Unsupported sort field `{field_name}`") from None

            expressions.append(attr.desc() if descending else attr.asc())

        return expressions

    def _parse_spec_key(self, key: str) -> tuple[list[str], str, str]:
        """Split a spec-mode key into relation path, field name, and operator name."""
        parts = key.split(RELATION_SPLITTER)
        terminal = parts[-1]
        field_name, operator_name = self._split_expression_key(terminal)
        return parts[:-1], field_name, operator_name or "eq"

    def _resolve_spec_field(
        self,
        spec: QuerySpec,
        relation_path: list[str],
        field_name: str,
    ) -> tuple[list[tuple[str, RelationFieldSpec]], ScalarFieldSpec]:
        """Resolve a nested spec field into relation segments and a terminal scalar field."""
        relation_specs: list[tuple[str, RelationFieldSpec]] = []
        current_fields = spec.filter_fields

        for relation_name in relation_path:
            field_spec = current_fields.get(relation_name)
            if not isinstance(field_spec, RelationFieldSpec):
                path = RELATION_SPLITTER.join(relation_path)
                raise SpecificationError(f"Unsupported relation path `{path}`")

            relation_specs.append((relation_name, field_spec))
            current_fields = field_spec.target_fields

        scalar_spec = current_fields.get(field_name)
        if not isinstance(scalar_spec, ScalarFieldSpec):
            path = RELATION_SPLITTER.join([*relation_path, field_name]).strip(RELATION_SPLITTER)
            raise AttributeResolutionError(f"Unsupported filter field `{path}`")

        return relation_specs, scalar_spec

    def _ensure_spec_alias(
        self,
        entity: Any,  # noqa: ANN401
        path: str,
        relation_spec: RelationFieldSpec,
        aliases: OrderedDict[str, tuple[AliasedClass, InstrumentedAttribute]],
    ) -> AliasedClass:
        """Create or reuse a path alias for a join-strategy spec relation."""
        if path in aliases:
            return aliases[path][0]

        relationship = getattr(entity, relation_spec.attr.key)
        alias = aliased(relationship.property.mapper.class_)
        aliases[path] = (alias, relationship)
        return alias

    def _resolve_relation_filter_strategy(
        self,
        relation_spec: RelationFieldSpec,
    ) -> str:
        """Validate and resolve the effective filtering strategy for a relation."""
        strategy = relation_spec.resolved_filter_strategy()
        if strategy == "has" and relation_spec.kind != "one":
            raise ConfigurationError(
                f"Relation `{relation_spec.attr.key}` cannot use `has` with kind `{relation_spec.kind}`.",
            )
        if strategy == "any" and relation_spec.kind != "many":
            raise ConfigurationError(
                f"Relation `{relation_spec.attr.key}` cannot use `any` with kind `{relation_spec.kind}`.",
            )
        return strategy

    def _split_expression_key(self, key: str) -> tuple[str, str | None]:
        """Split a key into attribute path and operator name if the suffix is a known operator."""
        if OPERATOR_SPLITTER not in key:
            return key, None

        attr_name, operator_name = key.rsplit(OPERATOR_SPLITTER, 1)
        if operator_name in self.operators or operator_name == JSON_OPERATOR:
            return attr_name, operator_name

        return key, None

    @staticmethod
    def _cast_json_expression(column: Any, value: Any) -> Any:  # noqa: ANN401
        """Cast a JSON traversal expression based on the comparison value type."""
        if isinstance(value, bool):
            return column.as_boolean()
        if isinstance(value, int):
            return column.as_integer()
        if isinstance(value, float):
            return column.as_float()
        if isinstance(value, str):
            return column.as_string()
        return column
