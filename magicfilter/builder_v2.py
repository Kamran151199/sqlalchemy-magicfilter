from __future__ import annotations

import logging
from collections import abc, OrderedDict
from typing import Any, Dict, List, Optional, Union, Callable, Type, Tuple

from sqlalchemy import select, asc, desc, inspect, UnaryExpression, Select, and_, JSON
from sqlalchemy.orm import aliased, joinedload, contains_eager, Query, InstrumentedAttribute, selectinload
from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.sql import operators

from magicfilter.operator import OPERATORS

# --- Constants ---
RELATION_SPLITTER = '___'
OPERATOR_SPLITTER = '__'
DESC_PREFIX = '-'
JSON_OPERATOR = 'json'

logger = logging.getLogger(__name__)

# --- Exceptions ---

class MagicFilterError(ValueError):
    """Base exception for all magicfilter related errors."""
    pass

class ConfigurationError(MagicFilterError):
    """Raised when the model or schema is misconfigured."""
    pass

class AttributeResolutionError(MagicFilterError):
    """Raised when an attribute or path cannot be resolved."""
    pass

class OperatorError(MagicFilterError):
    """Raised when an invalid operator is provided."""
    pass

# --- Internal Components ---

class AliasRegistry:
    """Manages SQLAlchemy aliases and their corresponding join clauses recursively."""

    def __init__(self, root_model: Type[Any]):
        self.root_model = root_model
        self.aliases: OrderedDict[str, Tuple[AliasedClass, InstrumentedAttribute]] = OrderedDict()

    def resolve_joins(self, paths: List[str]) -> None:
        """Process all paths and create necessary aliases recursively."""
        for path in set(paths):
            if RELATION_SPLITTER in path:
                self._make_aliases(self.root_model, '', path)

    def _make_aliases(self, entity: Any, parent_path: str, full_path: str) -> None:
        parts = full_path.split(RELATION_SPLITTER)
        current_entity = entity
        current_path_prefix = parent_path

        for i, part in enumerate(parts):
            # Form the full path to this segment
            segment_path = f"{current_path_prefix}{RELATION_SPLITTER}{part}" if current_path_prefix else part
            
            if segment_path not in self.aliases:
                try:
                    relationship = getattr(current_entity, part)
                    # Get the target class of the relationship
                    target_cls = relationship.property.mapper.class_
                    alias = aliased(target_cls)
                    self.aliases[segment_path] = (alias, relationship)
                except AttributeError:
                    # Not a relationship or attribute on this entity, might be a leaf field
                    break
            
            current_entity, _ = self.aliases[segment_path]
            current_path_prefix = segment_path

    def get_alias_target(self, path: str) -> Tuple[Any, str]:
        """Returns the alias (or root model) and the remaining attribute name for a given path."""
        if RELATION_SPLITTER not in path:
            return self.root_model, path
        
        parts = path.rsplit(RELATION_SPLITTER, 1)
        parent_path, attr_name = parts[0], parts[1]
        
        if parent_path in self.aliases:
            return self.aliases[parent_path][0], attr_name
        
        return self.root_model, path

class ExpressionResolver:
    """Handles resolution of columns, JSON paths, and operator applications."""

    def __init__(self, operators_map: Dict[str, Callable]):
        self.operators = operators_map

    def resolve_attribute(self, target: Any, attr_path: str) -> Tuple[Any, List[str]]:
        """
        Resolves a column from a target (model or alias) and a path string.
        Returns the base column and any remaining JSON parts.
        """
        cls = inspect(target).mapper.class_
        filterable = getattr(cls, 'filterable_attributes', [])
        
        if attr_path in filterable:
            return getattr(target, attr_path), []

        # Handle potential JSON path traversal
        if OPERATOR_SPLITTER in attr_path:
            parts = attr_path.split(OPERATOR_SPLITTER)
            for i in range(len(parts), 0, -1):
                prefix = OPERATOR_SPLITTER.join(parts[:i])
                if prefix in filterable:
                    column = getattr(target, prefix)
                    # Safety check: Avoid path traversal on non-JSON columns
                    if not isinstance(column.type, JSON):
                         raise AttributeResolutionError(
                             f"Attribute `{prefix}` does not support path traversal (not a JSON column?)"
                         )
                    return column, parts[i:]
        
        raise AttributeResolutionError(f"Attribute `{attr_path}` not found or not filterable on `{cls.__name__}`")

    def apply_operator(self, column: Any, op_name: Optional[str], value: Any) -> Any:
        """Applies a named operator (or default equality) to a column/expression."""
        if op_name is None:
            return column == value
        
        if op_name not in self.operators:
            raise OperatorError(f"Invalid operator `{op_name}`")
        
        return self.operators[op_name](column, value)

    def cast_json_expression(self, expression: Any, value: Any) -> Any:
        """Heuristically cast JSON expressions for cross-DB compatibility (e.g. SQLite)."""
        if isinstance(value, bool):
            return expression.as_boolean()
        if isinstance(value, int):
            return expression.as_integer()
        if isinstance(value, float):
            return expression.as_float()
        if isinstance(value, str):
            return expression.as_string()
        return expression

# --- Main Processor ---

class QueryBuilder:
    """
    Production-grade QueryBuilder for SQLAlchemy.
    Supports filtering, sorting, eager loading, and JSON field traversal.
    """

    def __init__(self, operators_map: Optional[Dict[str, Callable]] = None):
        self.operators = operators_map or OPERATORS
        self.resolver = ExpressionResolver(self.operators)

    def build(
        self,
        model: Type[Any],
        filters: Optional[Dict[Union[str, Callable], Any]] = None,
        sort_attrs: Optional[List[str]] = None,
        schema: Optional[Dict[InstrumentedAttribute, Any]] = None
    ) -> Select:
        """Orchestrates the query building process."""
        filters = filters or {}
        sort_attrs = sort_attrs or []
        
        query = select(model)
        
        # 1. Identify all required relationship paths for aliases
        all_paths = self._extract_relation_paths(filters, sort_attrs)
        
        # 2. Manage aliases and joins
        registry = AliasRegistry(model)
        registry.resolve_joins(all_paths)
        
        for alias, onclause in registry.aliases.values():
            query = query.outerjoin(target=alias, onclause=onclause)
            
        # 3. Apply Filters
        filter_expressions = self._build_filters(model, filters, registry)
        if filter_expressions:
            query = query.filter(*filter_expressions)
            
        # 4. Apply Sorting
        order_expressions = self._build_orders(model, sort_attrs, registry)
        if order_expressions:
            query = query.order_by(*order_expressions)
            
        # 5. Apply Eager Loading
        if schema:
            query = query.options(*self._build_eager_loading(schema, registry.aliases))
            
        return query

    def _extract_relation_paths(self, filters: Any, sort_attrs: List[str]) -> List[str]:
        """Extracts relationship paths from filters and sort attributes."""
        raw_keys = self._extract_raw_keys(filters)
        paths = raw_keys + [s.lstrip(DESC_PREFIX) for s in sort_attrs]
        return [p for p in paths if RELATION_SPLITTER in p]

    def _extract_raw_keys(self, filters: Any) -> List[str]:
        keys = []
        if isinstance(filters, abc.Mapping):
            for k, v in filters.items():
                if callable(k):
                    keys.extend(self._extract_raw_keys(v))
                else:
                    keys.append(k)
        elif isinstance(filters, abc.Sequence):
            for item in filters:
                keys.extend(self._extract_raw_keys(item))
        return keys

    def _build_filters(self, root_model: Type[Any], filters: Any, registry: AliasRegistry) -> List[Any]:
        if not filters:
            return []
            
        if isinstance(filters, abc.Mapping):
            expressions = []
            for key, value in filters.items():
                if callable(key):
                    # Handle logical operators like or_, and_
                    nested = self._build_filters(root_model, value, registry)
                    if nested:
                        expressions.append(key(*nested))
                else:
                    expressions.append(self._process_single_filter(key, value, registry))
            return expressions
        
        if isinstance(filters, abc.Sequence):
            return [e for f in filters for e in self._build_filters(root_model, f, registry)]
            
        raise MagicFilterError(f"Unsupported filter type: {type(filters)}")

    def _process_single_filter(self, key: str, value: Any, registry: AliasRegistry) -> Any:
        # Determine if we have an operator
        op_name = None
        attr_path = key
        
        if OPERATOR_SPLITTER in key:
            parts = key.rsplit(OPERATOR_SPLITTER, 1)
            # Check if the right part is a known operator OR the special 'json' operator
            if parts[1] in self.operators or parts[1] == JSON_OPERATOR:
                attr_path, op_name = parts[0], parts[1]
        
        # Resolve target entity (root or alias)
        target, attr_name = registry.get_alias_target(attr_path)
        
        # Resolve column and JSON path
        column, json_path = self.resolver.resolve_attribute(target, attr_name)
        
        # Handle JSON traversal
        for p in json_path:
            column = column[p]
            
        if op_name == JSON_OPERATOR:
            if not isinstance(value, dict):
                raise MagicFilterError(f"Operator `json` expects dict, got {type(value)}")
            
            sub_exprs = []
            for j_key, j_val in value.items():
                j_col = column
                for part in j_key.split(OPERATOR_SPLITTER):
                    j_col = j_col[part]
                j_col = self.resolver.cast_json_expression(j_col, j_val)
                sub_exprs.append(j_col == j_val)
            return and_(*sub_exprs) if sub_exprs else True
        
        # Normal operator application
        if json_path:
            column = self.resolver.cast_json_expression(column, value)
            
        return self.resolver.apply_operator(column, op_name, value)

    def _build_orders(self, root_model: Type[Any], sort_attrs: List[str], registry: AliasRegistry) -> List[UnaryExpression]:
        expressions = []
        for sort_key in sort_attrs:
            direction = asc
            field_path = sort_key
            
            if sort_key.startswith(DESC_PREFIX):
                direction = desc
                field_path = sort_key.lstrip(DESC_PREFIX)
            
            target, attr_name = registry.get_alias_target(field_path)
            
            # For ordering, we don't typically support JSON paths yet unless explicitly filterable
            # but we follow the same resolution logic
            cls = inspect(target).mapper.class_
            sortable = getattr(cls, 'sortable_attributes', [])
            
            if attr_name not in sortable:
                raise AttributeResolutionError(f"Attribute `{attr_name}` not sortable on `{cls.__name__}`")
            
            expressions.append(direction(getattr(target, attr_name)))
        return expressions

    def _build_eager_loading(
        self, 
        schema: Dict[InstrumentedAttribute, Any], 
        aliases: Dict[str, Tuple[AliasedClass, InstrumentedAttribute]],
        parent_path: str = '',
        parent_alias: Optional[AliasedClass] = None
    ) -> List[Any]:
        options = []
        for rel_attr, nested_schema in schema.items():
            rel_name = rel_attr.key
            current_path = f"{parent_path}{RELATION_SPLITTER}{rel_name}" if parent_path else rel_name
            
            # Determine join method
            join_method = joinedload
            if isinstance(nested_schema, tuple):
                join_method, nested_schema = nested_schema
            elif callable(nested_schema):
                join_method = nested_schema
                nested_schema = None

            if current_path in aliases:
                # Relationship already aliased for filtering/sorting
                target_alias, _ = aliases[current_path]
                option = contains_eager(getattr(parent_alias or rel_attr.class_, rel_name).of_type(target_alias))
                
                if nested_schema:
                    option = option.options(*self._build_eager_loading(nested_schema, aliases, current_path, target_alias))
                options.append(option)
            else:
                # Standard eager load
                target_cls = inspect(rel_attr).mapper.class_
                rel = getattr(parent_alias, rel_name) if parent_alias else rel_attr
                
                # Check if we need of_type
                if parent_alias and join_method != selectinload:
                    rel = rel.of_type(target_cls)
                
                option = join_method(rel)
                
                if nested_schema:
                    option = option.options(*self._build_eager_loading(nested_schema, aliases, current_path, target_cls if parent_alias else None))
                options.append(option)
                
        return options
