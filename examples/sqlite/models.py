from sqlalchemy import String, Integer, ForeignKey, Float, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import declarative_base

# Define the base for ORM models
Base = declarative_base()


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    age: Mapped[int] = mapped_column(Integer)

    profile: Mapped["Profile"] = relationship('Profile', back_populates='user', uselist=False)
    addresses: Mapped[list["UserAddress"]] = relationship('UserAddress', back_populates='user')
    reviews: Mapped[list["Review"]] = relationship('Review', back_populates='user')
    orders: Mapped[list["Order"]] = relationship('Order', back_populates='user')

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'name', 'age']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'name', 'age']


class UserAddress(Base):
    __tablename__ = 'user_addresses'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    address: Mapped[str] = mapped_column(String)
    user: Mapped["User"] = relationship('User', back_populates='addresses')

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'address', 'user_id']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'address', 'user_id']


class Profile(Base):
    __tablename__ = 'profiles'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    username: Mapped[str] = mapped_column(String)

    user: Mapped["User"] = relationship("User", back_populates="profile")

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'user_id', 'username']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'user_id', 'username']


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text)

    products: Mapped[list["Product"]] = relationship("Product", back_populates="category")

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'name']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'name']


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))

    category: Mapped["Category"] = relationship("Category", back_populates="products")
    reviews: Mapped[list["Review"]] = relationship("Review", back_populates="product")

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'name', 'price', 'category_id']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'name', 'price', 'category_id']


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    user: Mapped["User"] = relationship("User", back_populates="reviews")
    product: Mapped["Product"] = relationship("Product", back_populates="reviews")

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'rating', 'user_id', 'product_id']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'rating', 'user_id', 'product_id']


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="orders")
    products: Mapped[list["OrderProduct"]] = relationship("OrderProduct", back_populates="order")

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'order_number', 'user_id', 'total_amount']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'order_number', 'user_id', 'total_amount']


class OrderProduct(Base):
    __tablename__ = "order_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="products")
    product: Mapped["Product"] = relationship("Product")

    @classmethod
    @property
    def filterable_attributes(cls) -> list[str]:
        return ['id', 'order_id', 'product_id', 'quantity']

    @classmethod
    @property
    def sortable_attributes(cls) -> list[str]:
        return ['id', 'order_id', 'product_id', 'quantity']