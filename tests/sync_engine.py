import pytest
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from magicfilter import QueryBuilder

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    age = Column(Integer)
    profile = relationship('Profile', back_populates='user', uselist=False)
    created_at = Column(String)

    @classmethod
    @property
    def filterable_attributes(cls):
        return ['id', 'name', 'age', 'profile', 'created_at']

    @classmethod
    @property
    def sortable_attributes(cls):
        return ['id', 'name', 'age']


class Profile(Base):
    __tablename__ = 'profiles'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    bio = Column(String)
    user = relationship('User', back_populates='profile')

    @classmethod
    @property
    def filterable_attributes(cls):
        return ['id', 'bio', 'user_id', 'bio_length']

    @classmethod
    @property
    def sortable_attributes(cls):
        return ['id', 'bio', 'user_id']

    @hybrid_property
    def bio_length(self):
        return len(self.bio)

    @bio_length.expression
    def bio_length(cls):
        return func.length(cls.bio)

    @classmethod
    @property
    def hybrid_methods(cls):
        return ['bio_length']


@pytest.fixture(scope="module")
def engine():
    return create_engine('sqlite:///:memory:', echo=True)


@pytest.fixture(scope="module")
def session(engine):
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


@pytest.fixture(scope="module")
def query_builder():
    return QueryBuilder()


def test_build_simple_query(query_builder, session):
    query = query_builder.build(User)
    assert str(query) == 'SELECT users.id, users.name, users.age, users.created_at \nFROM users'


def test_build_query_with_filters(query_builder, session):
    filters = {"name": "John", "age__gt": 25}
    query = query_builder.build(User, filters=filters)
    assert "WHERE users.name = :name_1 AND users.age > :age_1" in str(query)


def test_build_query_with_sorting(query_builder, session):
    sort_attrs = ["-age", "name"]
    query = query_builder.build(User, sort_attrs=sort_attrs)
    assert "ORDER BY users.age DESC, users.name ASC" in str(query)


def test_build_query_with_relationships(query_builder, session):
    filters = {"profile___bio__contains": "engineer"}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "LEFT OUTER JOIN profiles AS profiles_1" in compiled
    assert "profiles_1.bio LIKE '%' || 'engineer' || '%')" in compiled


def test_build_query_with_or_condition(query_builder, session):
    from sqlalchemy import or_
    filters = {or_: [{"name__exact": "John"}, {"age__gt": 30}]}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "WHERE users.name = 'John' OR users.age > 30" in compiled


def test_build_query_with_hybrid_property(query_builder, session):
    filters = {"profile___bio_length__gt": 100}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "length(profiles_1.bio) > 100" in compiled


def test_build_query_with_eager_loading(query_builder, session):
    from sqlalchemy.orm import joinedload
    schema = {User.profile: joinedload}
    query = query_builder.build(User, schema=schema)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "LEFT OUTER JOIN profiles AS profiles_1" in compiled


def test_build_query_with_nested_eager_loading(query_builder, session):
    from sqlalchemy.orm import joinedload, selectinload
    schema = {User.profile: (joinedload, {Profile.user: selectinload})}
    query = query_builder.build(User, schema=schema)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "LEFT OUTER JOIN profiles AS profiles_1" in compiled


def test_invalid_filter_attribute(query_builder, session):
    filters = {"invalid_attr__eq": "value"}
    with pytest.raises(ValueError):
        query_builder.build(User, filters=filters)


def test_invalid_sort_attribute(query_builder, session):
    sort_attrs = ["invalid_attr"]
    with pytest.raises(ValueError):
        query_builder.build(User, sort_attrs=sort_attrs)


def test_invalid_operator(query_builder, session):
    filters = {"name__invalid_op": "value"}
    with pytest.raises(ValueError):
        query_builder.build(User, filters=filters)


def test_build_query_with_all_features(query_builder, session):
    from sqlalchemy import or_
    from sqlalchemy.orm import joinedload

    filters = {
        "name__startswith": "J",
        "age__between": [25, 35],
        or_: [{"profile___bio__contains": "engineer"}, {"profile___bio__contains": "developer"}],
    }
    sort_attrs = ["-age", "name"]
    schema = {User.profile: joinedload}

    query = query_builder.build(User, filters=filters, sort_attrs=sort_attrs, schema=schema)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "LEFT OUTER JOIN profiles AS profiles_1" in compiled
    assert "users.name LIKE 'J' || '%'" in compiled
    assert "users.age BETWEEN 25 AND 35" in compiled
    assert "(profiles_1.bio LIKE '%' || 'engineer' || '%') OR (profiles_1.bio LIKE '%' || 'developer' || '%')" in compiled
    assert "ORDER BY users.age DESC, users.name ASC" in compiled


def test_multiple_conditions_on_same_field(query_builder, session):
    filters = {"age__ge": 20, "age__lt": 30}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "users.age >= 20 AND users.age < 30" in compiled


def test_in_operator(query_builder, session):
    filters = {"age__in": [25, 30, 35]}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "WHERE users.age IN (25, 30, 35)" in compiled


def test_not_in_operator(query_builder, session):
    filters = {"age__notin": [25, 30, 35]}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "users.age NOT IN (25, 30, 35)" in compiled


def test_is_null_condition(query_builder, session):
    filters = {"profile__isnull": True}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "NOT (EXISTS (SELECT 1 \nFROM profiles \nWHERE users.id = profiles.user_id))" in compiled


def test_is_not_null_condition(query_builder, session):
    filters = {"profile__isnull": False}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "EXISTS (SELECT 1 \nFROM profiles \nWHERE users.id = profiles.user_id)" in compiled


def test_complex_or_and_conditions(query_builder, session):
    from sqlalchemy import or_, and_
    filters = {
        or_: [
            {
                and_: [
                    {'name__startswith': 'A'},
                    {'age__ge': 30}
                ]
            },
            {
                and_: [
                    {'name__startswith': 'B'},
                    {'age__ge': 30}
                ]
            }
        ],
    }
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "(users.name LIKE 'A' || '%') AND users.age >= 30 OR (users.name LIKE 'B' || '%') AND users.age >= 30" in compiled


def test_multiple_sort_with_relationships(query_builder, session):
    sort_attrs = ["-age", "profile___bio", "-name"]
    query = query_builder.build(User, sort_attrs=sort_attrs)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "ORDER BY users.age DESC, profiles_1.bio ASC, users.name DESC" in compiled


def test_filter_by_relationship_id(query_builder, session):
    filters = {"profile___id": 1}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "WHERE profiles_1.id = 1" in compiled


def test_filter_with_different_operators_on_relationship(query_builder, session):
    filters = {
        "profile___bio__startswith": "Senior",
        "profile___bio__endswith": "developer",
        "profile___bio__contains": "Python"
    }
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "(profiles_1.bio LIKE 'Senior' || '%') AND (profiles_1.bio LIKE '%' || 'developer') AND (profiles_1.bio LIKE '%' || 'Python' || '%')" in compiled


def test_filter_with_date_operations(query_builder, session):
    from datetime import date
    filters = {
        "created_at__year": 2023,
        "created_at__month": 6,
        "created_at__day": 15,
        "created_at__ge": date(2023, 1, 1),
        "created_at__lt": date(2024, 1, 1)
    }
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "EXTRACT(year FROM users.created_at) = 2023" in compiled
    assert "AND EXTRACT(month FROM users.created_at) = 6" in compiled
    assert "AND EXTRACT(day FROM users.created_at) = 15" in compiled
    assert "AND users.created_at >= '2023-01-01'" in compiled
    assert "AND users.created_at < '2024-01-01'" in compiled


def test_filter_with_subquery(query_builder, session):
    from sqlalchemy import select
    subquery = select(Profile.user_id).where(Profile.bio.like("%Python%"))
    filters = {"id__in": subquery}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "users.id IN (SELECT profiles.user_id \nFROM profiles \nWHERE profiles.bio LIKE '%Python%')" in compiled


def test_filter_with_case_insensitive_like(query_builder, session):
    filters = {"name__ilike": "john"}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "WHERE lower(users.name) LIKE lower('john')" in compiled


def test_complex_nested_relationships(query_builder, session):
    filters = {"profile___user___profile___bio__contains": "recursive"}
    query = query_builder.build(User, filters=filters)
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "LEFT OUTER JOIN profiles AS profiles_1 ON users.id = profiles_1.user_id" in compiled
    assert "LEFT OUTER JOIN users AS users_1 ON profiles_1.user_id = users_1.id" in compiled \
        or "LEFT OUTER JOIN users AS users_1 ON users_1.id = profiles_1.user_id" in compiled
    assert "LEFT OUTER JOIN profiles AS profiles_2 ON users_1.id = profiles_2.user_id" in compiled
    assert "profiles_2.bio LIKE '%' || 'recursive' || '%'" in compiled
