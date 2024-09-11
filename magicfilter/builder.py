from __future__ import annotations

from collections import abc, OrderedDict
from typing import Any, Dict, List, Optional, Union, Callable

from sqlalchemy import select, asc, desc, inspect, UnaryExpression, Select
from sqlalchemy.orm import aliased, joinedload, contains_eager, Query, InstrumentedAttribute, selectinload
from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.sql import operators

from magicfilter.operator import OPERATORS

RELATION_SPLITTER = '___'
OPERATOR_SPLITTER = '__'
DESC_PREFIX = '-'


class QueryBuilder:
    """
    A class to build SQLAlchemy queries based on the provided model, filters, sort attributes, and schema.
    It uses magic filters to apply filters to the query and eager loading to load nested relationships.

    Magic filters are constructed using the following format:
    - `field___relationship-or-field__operator`: To filter by a field in a relationship or the root entity.

    !!! IMPORTANT !!!
    In `schema` parameter, a user can specify how to eager load nested relationships - joinedload or selectinload or subqueryload.
    But if the relationship is used in filters or sort_attrs, it will be automatically joined - meaning it will be joinedload.

    It is inspired by django-filter and django-rest-framework's filtering system.
    """

    def __init__(self):
        self.operators = OPERATORS

    def _eager_expr_from_schema(self, schema: Dict[InstrumentedAttribute, Any],
                                aliases: Optional[Dict[str, Any]] = None,
                                parent_path: str = '',
                                parent_entity: Any = None
                                ) -> List[Any]:
        """
        Constructs a list of eager expressions based on the provided schema.

        :param schema: Schema dictionary representing the nested relationships to be eager loaded.
        :param aliases: A dictionary of aliases to avoid duplicate eager loading of the same relationship,
            if it's already loaded due to usage in filters or sort_attrs.
        :param parent_path: The parent path to the current relationship.
        :param parent_entity: The parent entity to use for of_type when parent is aliased.
        :return: A list of eager expressions.
        """
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
                        *self._eager_expr_from_schema(nested_schema, aliases, current_path, parent_entity=rel_alias)
                    )

                eager_options.append(nested_expr)
            else:
                if parent_entity and isinstance(parent_entity, AliasedClass):
                    # If parent is aliased, we need to get the corresponding relationship from the alias
                    rel = getattr(parent_entity, rel_name)
                else:
                    rel = relationship_attr

                # Use inspect to get the mapper and then the class
                target_cls = inspect(rel).mapper.class_

                if join_method == selectinload:
                    # For selectinload, we don't need to use of_type
                    nested_expr = join_method(rel)
                else:
                    # For other loading methods (like joinedload), we use of_type if parent is aliased
                    nested_expr = join_method(
                        rel.of_type(target_cls) if isinstance(parent_entity, AliasedClass) else rel)

                if nested_schema is not None:
                    nested_expr = nested_expr.options(
                        *self._eager_expr_from_schema(
                            nested_schema, aliases, current_path,
                            parent_entity=target_cls if isinstance(parent_entity, AliasedClass) else None
                        )
                    )

                eager_options.append(nested_expr)

        return eager_options

    def build(
            self,
            model: Any,
            filters: Optional[Dict[Union[str, Callable], Any]] = None,
            sort_attrs: Optional[List[str]] = None,
            schema: Optional[Dict[InstrumentedAttribute, Any]] = None
    ) -> Union[Query, Select]:
        """
        Builds an SQLAlchemy query based on the provided model, filters, sort attributes, and schema.


        :param model: The root SQLAlchemy model to build the query from.
            This is usually the first table to be selected in the query (SELECT ... FROM model ...).

        :param filters: A dictionary containing the magic filters to be applied to the query (e.g. {"name__eq": "John"}).

        :param sort_attrs: A list of strings representing the attributes to sort by. Prefixing an attribute with a minus
            sign (-) will sort in descending order.

        :param schema: A dictionary representing the nested relationships to be eager loaded. The keys are the
            relationship attributes and the values are either "tuples with the join method callable and a nested schema"
            or a "join method callable only" or a "dictionary with the nested schema only".

        :return: An SQLAlchemy query object.
        """
        filters = filters or {}
        sort_attrs = sort_attrs or []

        query = select(model)
        root_cls = model

        # get all unique fields/attributes from filters and sort_attrs
        attrs = list(set(list(self._extract_keys(filters)) + [s.lstrip(DESC_PREFIX) for s in sort_attrs]))

        # create aliases of tables for each table the fields are part of.
        aliases = OrderedDict()
        self._make_aliases_for_tables(root_cls, '', attrs, aliases)

        # left join all the tables that have been aliased using alias and on_clause from the alias dict.
        for table_alias, on_clause in aliases.values():
            query = query.outerjoin(target=table_alias, onclause=on_clause)

        # apply filters to the query
        filter_expr = self._make_filters(filters, root_cls, aliases)
        query = query.filter(*filter_expr)

        # apply sorting to the query
        query = query.order_by(*self._make_orders(sort_attrs, root_cls, aliases))

        # if schema is provided, apply eager loading to the query based on the schema.
        # e.g. {User.profile: {Profile.images: JOINED}} will eager load the profile and images relationship.
        if schema:
            query = query.options(*self._eager_expr_from_schema(schema, aliases=aliases))

        return query

    def _extract_keys(self, filters: Union[Dict[str, Any], List[Any]]) -> List[str]:
        """
        Extract all keys from the filters dict.

        E.g. {"name__eq": "John", "age__gt": 25, or_: {"age__lt": 30, "age__gt": 20}}
            will return ['name__eq', 'age__gt', 'age__lt', 'age__gt'] -> should be deduplicated outside of this function.

        :param filters: The filters dict to extract keys from.
        :return: A list of keys.
        """
        keys = []
        if isinstance(filters, abc.Mapping):
            for key, value in filters.items():
                if callable(key):
                    keys.extend(self._extract_keys(value))
                else:
                    keys.append(key)
        elif isinstance(filters, abc.Sequence):
            for f in filters:
                keys.extend(self._extract_keys(f))
        else:
            raise ValueError(f"Unsupported type ({type(filters)}) in filters: {filters}")
        return keys

    def _make_aliases_for_tables(self, entity: Any, entity_path: str, attrs: List[str],
                                 aliases: OrderedDict) -> None:
        """
        Constructs a dict of aliases for each referenced relationship in the attrs/fields list.

        For example, if the attrs list contains ['profile___name', 'profile___images___url'],
        this function will create aliases for the profile table and the images table,
        where the key is the actual filter key and the value is a tuple of the table alias and on_clause.

        e.g. {'profile': (aliased(Profile), User.profile), 'profile___images': (aliased(ProfileImage), Profile.images)}

        :param entity: The entity/model/table/relation to attach the relationships to.
        :param entity_path: The path to the entity. In case of profile___images, the entity_path is 'profile', since
            the images should be attached to the profile entity.
        :param attrs: The list of fields to create aliases for.
        :param aliases: The dictionary to store the aliases in.
        :return: None
        """
        relations = {}
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

    def _make_filters(self, _filters: Union[Dict[str, Any], List[Any]], root_cls: Any, aliases: Dict[str, Any]):
        """
        Constructs a list of filter expressions based on the provided filters dict attaching them to their respective tables.

        !!! IMPORTANT !!!

        This is not same as the _make_entity_filters method. This method is used to construct filter expressions for all entities in the query,
        while using the _make_entity_filters method to construct filter expressions for a single entity.

        :param _filters: The filters dict to construct the filter expressions from.
        :param root_cls: The root entity/model/table to attach the filters to.
        :param aliases: The dictionary of aliases to attach the filters to. The keys are the filter keys and the values are
            tuples of the table (alias, on_clause). E.g. {'profile___name': (aliased(Profile), User.profile)}
        :return: A list of filter expressions.
        """

        if isinstance(_filters, abc.Mapping):
            expressions = []
            for attr, value in _filters.items():
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
        elif isinstance(_filters, abc.Sequence):
            return [expr for f in _filters for expr in self._make_filters(f, root_cls, aliases)]
        else:
            raise ValueError(f"Unsupported type ({type(_filters)}) in filters: {_filters}")

    def _make_entity_filters(self, cls_or_alias: Union[AliasedClass, Any], **filters: Any):
        """
        Given the entity-class or alias, and a dictionary of filters, constructs a list of filter expressions for the entity,
        by applying correct operators based on the last part of the filter key.

        e.g. for a filter key 'name__eq', the operator 'eq' will be applied to the 'name' column.

        :param cls_or_alias: The entity-class or alias to attach the filters to.
        :param filters: The filters dict to construct the filter expressions from.
        :return: A list of filter expressions.
        """

        mapper = cls_or_alias if isinstance(cls_or_alias, AliasedClass) else cls_or_alias
        cls = inspect(cls_or_alias).mapper.class_
        hybrid_methods = getattr(cls, 'hybrid_methods', [])
        filterable_attributes = getattr(cls, 'filterable_attributes', [])

        expressions = []
        for attr, value in filters.items():
            if attr in hybrid_methods:
                method = getattr(cls, attr)
                expressions.append(method(mapper, value))
            else:
                if OPERATOR_SPLITTER in attr:
                    attr_name, op_name = attr.rsplit(OPERATOR_SPLITTER, 1)
                    if op_name not in self.operators:
                        raise ValueError(f'Expression `{attr}` has incorrect operator `{op_name}`')
                    op = self.operators[op_name]
                else:
                    attr_name, op = attr, operators.eq

                if attr_name not in filterable_attributes:
                    raise ValueError(f'Expression `{attr}` has incorrect attribute `{attr_name}`')

                column = getattr(mapper, attr_name)
                expressions.append(op(column, value))

        return expressions

    def _make_orders(self, orders: List[str], root_cls: Any, aliases: Dict[str, Any]) -> List[UnaryExpression]:
        """
        Constructs a list of order expressions based on the provided orders list.

        !!! IMPORTANT !!!

        This is not same as the _make_entity_orders method. This method is used to construct order expressions for all entities in the query,
        while using the _make_entity_orders method to construct order expressions for a single entity.

        :param orders: List of order fields.
        :param root_cls: The root entity/model/table to attach the orders to.
        :param aliases: The dictionary of aliases to attach the orders to. The keys are the order keys and the values are
            tuples of the table (alias, on_clause). E.g. {'profile___name': (aliased(Profile), User.profile)}
        :return: List of order expressions.
        """
        order_expressions = []
        for field in orders:
            prefix = ''
            if field.startswith(DESC_PREFIX):
                prefix, field = DESC_PREFIX, field.lstrip(DESC_PREFIX)
            parts = field.rsplit(RELATION_SPLITTER, 1)
            if RELATION_SPLITTER in field:
                entity, field_name = aliases[parts[0]][0], prefix + parts[1]
            else:
                entity, field_name = root_cls, prefix + field
            try:
                order_expressions.extend(self._make_entity_orders(entity, field_name))
            except KeyError as e:
                raise ValueError(f"Incorrect order path `{field}`: {e}")
        return order_expressions

    @staticmethod
    def _make_entity_orders(cls_or_alias: Union[AliasedClass, Any], *columns: str) -> List[UnaryExpression]:
        """
        Given the entity-class or alias, and a list of columns, constructs a list of order expressions for the entity,
        by applying correct order functions based on the column names.

        :param cls_or_alias: The entity-class or alias to attach the orders to.
        :param columns: List of columns to order by.
        :return: List of order expressions.
        """
        mapper = cls_or_alias if isinstance(cls_or_alias, AliasedClass) else cls_or_alias
        cls = inspect(cls_or_alias).mapper.class_
        expressions = []
        for attr in columns:
            fn, attr = (desc, attr[1:]) if attr.startswith(DESC_PREFIX) else (asc, attr)
            if attr not in cls.sortable_attributes:
                raise ValueError(f'Cannot order {cls} by {attr}')
            expressions.append(fn(getattr(mapper, attr)))
        return expressions
