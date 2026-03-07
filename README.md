# MagicFilter

[![Build Status](https://github.com/devaccelclub/sqlalchemy-magicfilter/actions/workflows/run-tests.yml/badge.svg)](https://github.com/devaccelclub/sqlalchemy-magicfilter/actions/workflows/run-tests.yml)

MagicFilter is a powerful and flexible filtering library for SQLAlchemy queries. It provides an intuitive way to build complex queries with support for nested relationships, custom operators, and eager loading.

## Features

- Easy-to-use query building with support for complex filters
- Automatic handling of nested relationships
- Custom operator support
- Eager loading of related objects
- Optional explicit `QuerySpec` mode for strict, repo-owned contracts
- Optional explicit `LoadSpec` mode for eager-loading shape
- Explicit per-relation filter strategy control in `QuerySpec` mode
- Inspired by Django's filtering system

## Installation

You can install MagicFilter using pip:

```bash
pip install magicfilter
```

Or if you're using Poetry:

```bash
poetry add magicfilter
```

## Quick Start

Here's a simple example of how to use MagicFilter:

```python
from magicfilter import QueryBuilder
from sqlalchemy.orm import joinedload
from your_sqlalch_models import User, Profile

query_builder = QueryBuilder()

filters = {
    "name__startswith": "John",
    "age__gte": 30,
    "profile___bio__contains": "engineer"
}

sort_attrs = ["-age", "name"]

schema = {
    User.profile: {
        Profile.posts: joinedload
    }
}

query = query_builder.build(
    model=User,
    filters=filters,
    sort_attrs=sort_attrs,
    schema=schema
)

...
```

This will create a query that:
1. Filters for users whose name starts with "John", are 30 or older, and have "engineer" in their profile bio
2. Sorts the results by age (descending) and then by name (ascending)
3. Eager loads the user's profile and associated posts

## Usage

### QueryBuilder

The `QueryBuilder` class is the main interface for building queries. It provides the following method:

#### `build(model, filters=None, sort_attrs=None, schema=None, *, spec=None, load_spec=None)`

- `model`: The SQLAlchemy model to query
- `filters`: A dictionary of filters to apply
- `sort_attrs`: A list of attributes to sort by
- `schema`: A legacy dictionary describing how to eager load related objects
- `spec`: An explicit query contract for strict repository-style usage
- `load_spec`: An explicit eager-loading contract. Prefer this over `schema` in new code.

### Filters

Filters are specified using a dictionary where the keys are strings in the format `"field___relationship__operator"` and the values are the filter values.

Available operators:

- `eq` / `exact`
- `isnull`
- `ne` (not equal)
- `gt` (greater than)
- `ge` / `gte` (greater than or equal)
- `lt` (less than)
- `le` / `lte` (less than or equal)
- `in`
- `notin`
- `between`
- `like`
- `ilike`
- `startswith`
- `istartswith`
- `endswith`
- `iendswith`
- `contains`
- `icontains`
- `regex`
- `year`
- `month`
- `day`

If no operator is specified, equality is used by default.

### Sorting

Sorting is specified using a list of strings. Prefix a field with `-` for descending order.

### Schema / Load Shape

The legacy `schema` argument is a dictionary that describes how to eager load related objects. It uses SQLAlchemy's relationship attributes as keys and can be nested.

For new code, prefer `LoadSpec`:

```python
from magicfilter import LoadFieldSpec, LoadSpec

load_spec = LoadSpec(
    fields=(
        LoadFieldSpec(attr=User.profile, strategy="joined"),
    ),
)

query = QueryBuilder().build(
    model=User,
    load_spec=load_spec,
)
```

Supported load strategies:

- `joined`
- `selectin`

### Strict QuerySpec Mode

For repository-style usage where you want explicit ownership of what is filterable and sortable, pass a `QuerySpec` to `build(...)`.

```python
from magicfilter import QueryBuilder, QuerySpec, RelationFieldSpec, ScalarFieldSpec

user_spec = QuerySpec(
    root_model=User,
    filter_fields={
        "name": ScalarFieldSpec(User.name, frozenset({"eq", "startswith", "icontains"})),
        "age": ScalarFieldSpec(User.age, frozenset({"eq", "gt", "gte", "lt", "lte"})),
        "profile": RelationFieldSpec(
            User.profile,
            kind="one",
            filter_strategy="auto",
            target_fields={
                "bio": ScalarFieldSpec(Profile.bio, frozenset({"eq", "icontains"})),
            },
        ),
    },
    sort_fields={
        "name": User.name,
        "age": User.age,
    },
)

query = QueryBuilder().build(
    model=User,
    filters={"profile___bio__icontains": "engineer"},
    sort_attrs=["-age"],
    spec=user_spec,
)
```

In `QuerySpec` mode:

- only declared fields are queryable
- only declared sort fields are sortable
- relation filters compile through the declared strategy for each relation
- invalid fields and operators fail deterministically

### Relation Filter Strategy

`RelationFieldSpec` supports an explicit `filter_strategy`:

- `auto`
- `join`
- `has`
- `any`

`auto` resolves to:

- `kind="one"` -> `.has(...)`
- `kind="many"` -> `.any(...)`

Use `join` only when you explicitly want join-shaped SQL. The default `auto` strategy is safer for count semantics and duplicate-row avoidance.
