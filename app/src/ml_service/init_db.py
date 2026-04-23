from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from ml_service.database import create_schema, get_engine, get_session_factory
from ml_service.models import MLModel, Transaction, TransactionType, User, UserRole
from ml_service.security import hash_password
from ml_service.services import BalanceService, MLModelService, UserService


DEMO_USERS = (
    {
        "email": "demo-user@example.com",
        "password": "demo-user-password",
        "role": UserRole.USER,
        "initial_balance": Decimal("120.00"),
        "seed_marker": "seed:demo-user:balance",
    },
    {
        "email": "demo-admin@example.com",
        "password": "demo-admin-password",
        "role": UserRole.ADMIN,
        "initial_balance": Decimal("300.00"),
        "seed_marker": "seed:demo-admin:balance",
    },
)

DEMO_MODELS = (
    {
        "name": "demo_model",
        "version": "1.0",
        "description": "Task 5 demo model for RabbitMQ worker processing",
        "cost_per_prediction": Decimal("2.50"),
    },
    {
        "name": "priority-classifier",
        "version": "1.0",
        "description": "Demo security finding priority classifier",
        "cost_per_prediction": Decimal("2.50"),
    },
    {
        "name": "priority-classifier",
        "version": "1.1",
        "description": "Updated demo security finding priority classifier",
        "cost_per_prediction": Decimal("3.00"),
    },
)


def initialize_database(
    engine: Engine | None = None,
    session_factory: sessionmaker | None = None,
) -> None:
    database_engine = engine
    if database_engine is None and session_factory is not None:
        database_engine = session_factory.kw.get("bind")
    if database_engine is None:
        database_engine = get_engine()

    create_schema(database_engine)
    factory = session_factory or get_session_factory()

    with factory() as session:
        for demo_user in DEMO_USERS:
            user = session.scalar(select(User).where(User.email == demo_user["email"]))
            expected_password_hash = hash_password(demo_user["password"])
            if user is None:
                user = UserService.create_user(
                    session,
                    email=demo_user["email"],
                    password_hash=expected_password_hash,
                    role=demo_user["role"],
                    initial_balance=Decimal("0.00"),
                )
            else:
                user.password_hash = expected_password_hash
                user.role = demo_user["role"]
                session.commit()
                session.refresh(user)

            existing_seed = session.scalar(
                select(Transaction).where(
                    Transaction.user_id == user.id,
                    Transaction.type == TransactionType.TOP_UP,
                    Transaction.review_comment == demo_user["seed_marker"],
                )
            )
            if existing_seed is None and demo_user["initial_balance"] > Decimal("0.00"):
                BalanceService.top_up(
                    session,
                    user_id=user.id,
                    amount=demo_user["initial_balance"],
                    review_comment=demo_user["seed_marker"],
                )

        for demo_model in DEMO_MODELS:
            existing_model = session.scalar(
                select(MLModel).where(
                    MLModel.name == demo_model["name"],
                    MLModel.version == demo_model["version"],
                )
            )
            if existing_model is None:
                MLModelService.create_model(session, **demo_model)
