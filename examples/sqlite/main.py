import uuid

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from examples.sqlite.models import Base, User, Profile, UserAddress, Category, Product, Review, Order, OrderProduct
from magicfilter import QueryBuilder

# Asynchronous database setup
DATABASE_URL = "sqlite+aiosqlite:///database.db"
engine = create_async_engine(DATABASE_URL, echo=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def generate_unique_order_number():
    return f"ORD-{uuid.uuid4().hex[:8].upper()}"


async def seed_data(session: AsyncSession):
    # Create users with profiles and addresses
    user1 = User(name="John Doe", age=30, profile=Profile(username="johndoe"))
    user2 = User(name="Jane Doe", age=25, profile=Profile(username="janedoe"))

    address1 = UserAddress(address="123 Main St.")
    address2 = UserAddress(address="456 Elm St.")

    user1.addresses.append(address1)
    user2.addresses.append(address2)

    # Create categories and products
    category1 = Category(name="Electronics", description="Electronic devices and accessories")
    category2 = Category(name="Books", description="Physical and digital books")

    product1 = Product(name="Smartphone", price=599.99, description="Latest model smartphone", category=category1)
    product2 = Product(name="Laptop", price=999.99, description="High-performance laptop", category=category1)
    product3 = Product(name="Python Programming", price=39.99, description="Comprehensive Python guide",
                       category=category2)

    # Create reviews
    review1 = Review(rating=5, comment="Great product!", user=user1, product=product1)
    review2 = Review(rating=4, comment="Good value for money", user=user2, product=product2)

    # Create orders with unique order numbers
    order1 = Order(order_number=generate_unique_order_number(), user=user1, total_amount=599.99)
    order2 = Order(order_number=generate_unique_order_number(), user=user2, total_amount=1039.98)
    order3 = Order(order_number=generate_unique_order_number(), user=user1, total_amount=39.99)

    # Create order products
    order_product1 = OrderProduct(order=order1, product=product1, quantity=1)
    order_product2 = OrderProduct(order=order2, product=product2, quantity=1)
    order_product3 = OrderProduct(order=order2, product=product3, quantity=1)
    order_product4 = OrderProduct(order=order3, product=product3, quantity=1)

    session.add_all([user1, user2, category1, category2, product1, product2, product3,
                     review1, review2, order1, order2, order3,
                     order_product1, order_product2, order_product3, order_product4])
    await session.commit()


async def query_user_orders():
    async with async_session() as session:
        user_filters = {
            'name__like': '%Doe%',
            'age__ge': 25,
            'orders___total_amount__ge': 1039.98,
        }
        user_sort_attrs = ["name"]
        user_schema = {
            User.orders: (
                selectinload,
                {
                    Order.products: (
                        joinedload, {
                            OrderProduct.product: (
                                joinedload,
                                {
                                    Product.category: joinedload,
                                    Product.reviews: joinedload
                                },
                            )
                        })
                },
            ),
            User.profile: joinedload
        }

        qb = QueryBuilder()
        user_query = qb.build(User, user_filters, user_sort_attrs, schema=user_schema)

        result = await session.execute(user_query)
        users = result.unique().scalars().all()

        print("Users and their orders:")
        for user in users:
            print(f"User: {user.name} (Username: {user.profile.username})")
            if user.orders:
                print("  Orders:")
                for order in user.orders:
                    print(f"    - Order Number: {order.order_number}, Total Amount: ${order.total_amount:.2f}")
            else:
                print("  No orders")
            print("---")


async def main(init_data: bool = False):
    await create_tables()
    if init_data:
        async with async_session() as session:
            await seed_data(session)

    await query_user_orders()


if __name__ == "__main__":
    from argparse import ArgumentParser
    import asyncio

    parser = ArgumentParser()
    parser.add_argument("--init-data", action="store_true", help="Initialize the database with sample data")
    asyncio.run(main(**vars(parser.parse_args())))


# Run the script with the --init-data flag to seed the database with sample data:
# $ python main.py --init-data