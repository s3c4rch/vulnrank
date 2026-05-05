from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ml_service.database import get_db_session
from ml_service.dependencies import get_current_admin, get_current_user
from ml_service.models import User
from ml_service.schemas import BalanceOperationResponse, BalanceView, TopUpRequest
from ml_service.serializers import serialize_balance, serialize_transaction
from ml_service.services import BalanceService, UserService


router = APIRouter(tags=["balance"])


@router.get("/balance", response_model=BalanceView)
def get_balance(current_user: User = Depends(get_current_user)) -> BalanceView:
    return serialize_balance(current_user)


@router.post("/balance/top-up", response_model=BalanceOperationResponse)
def top_up_balance(
    payload: TopUpRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> BalanceOperationResponse:
    transaction = BalanceService.top_up(
        session,
        user_id=current_user.id,
        amount=payload.amount,
        review_comment="balance top-up via api",
    )
    updated_user = UserService.get_user(session, current_user.id)
    return BalanceOperationResponse(
        balance=serialize_balance(updated_user),
        transaction=serialize_transaction(transaction),
    )


@router.post("/admin/users/{user_id}/balance/top-up", response_model=BalanceOperationResponse, tags=["admin"])
def admin_top_up_user(
    user_id: str,
    payload: TopUpRequest,
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> BalanceOperationResponse:
    transaction = BalanceService.top_up(
        session,
        user_id=user_id,
        amount=payload.amount,
        review_comment="balance top-up via admin web",
    )
    updated_user = UserService.get_user(session, user_id)
    return BalanceOperationResponse(
        balance=serialize_balance(updated_user),
        transaction=serialize_transaction(transaction),
    )
