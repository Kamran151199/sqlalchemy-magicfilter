from sqlalchemy import extract
from sqlalchemy.sql import operators


def isnull(column, value):
    return column == None if value else column != None

def year(column, value):
    return extract('year', column) == value


def month(column, value):
    return extract('month', column) == value


def day(column, value):
    return extract('day', column) == value

def between(column, value):
    return column.between(value[0], value[1])

OPERATORS = {
    'isnull': isnull,
    'exact': operators.eq,
    'ne': operators.ne,
    'gt': operators.gt,
    'ge': operators.ge,
    'lt': operators.lt,
    'le': operators.le,
    'in': operators.in_op,
    'notin': operators.notin_op,
    'between': between,
    'like': operators.like_op,
    'ilike': operators.ilike_op,
    'startswith': operators.startswith_op,
    'istartswith': operators.istartswith_op,
    'endswith': operators.endswith_op,
    'iendswith': operators.iendswith_op,
    'contains': operators.contains_op,
    'icontains': operators.contains_op,
    'regex': operators.regexp_match_op,
    'year': year,
    'month': month,
    'day': day,
}
