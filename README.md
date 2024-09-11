# MagicFilter

[![Build Status](https://github.com/devaccelclub/sqlalchemy-magicfilter/actions/workflows/run-tests.yml/badge.svg)](https://github.com/devaccelclub/sqlalchemy-magicfilter/actions/workflows/run-tests.yml)

MagicFilter is a powerful and flexible filtering library for SQLAlchemy queries. It provides an intuitive way to build complex queries with support for nested relationships, custom operators, and eager loading.

## Features

- Easy-to-use query building with support for complex filters
- Automatic handling of nested relationships
- Custom operator support
- Eager loading of related objects
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

#### `build(model, filters=None, sort_attrs=None, schema=None)`

- `model`: The SQLAlchemy model to query
- `filters`: A dictionary of filters to apply
- `sort_attrs`: A list of attributes to sort by
- `schema`: A dictionary describing how to eager load related objects

### Filters

Filters are specified using a dictionary where the keys are strings in the format `"field___relationship__operator"` and the values are the filter values.

Available operators:

- `isnull`
- `exact` (default if no operator is specified)
- `ne` (not equal)
- `gt` (greater than)
- `ge` (greater than or equal)
- `lt` (less than)
- `le` (less than or equal)
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

### Sorting

Sorting is specified using a list of strings. Prefix a field with `-` for descending order.

### Schema

The schema is a dictionary that describes how to eager load related objects. It uses SQLAlchemy's relationship attributes as keys and can be nested.
