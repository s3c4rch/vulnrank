from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ml_service.database import get_db_session
from ml_service.dependencies import get_current_admin, get_current_user
from ml_service.models import User
from ml_service.schemas import HistoryResponse, TransactionHistoryResponse
from ml_service.serializers import serialize_prediction_history_item, serialize_transaction
from ml_service.services import PredictionService, TransactionService


router = APIRouter(tags=["history"])


@router.get("/history/predictions", response_model=HistoryResponse)
def get_prediction_history(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> HistoryResponse:
    tasks = PredictionService.get_prediction_history(session, current_user.id)
    return HistoryResponse(items=[serialize_prediction_history_item(task) for task in tasks])


@router.get("/history/transactions", response_model=TransactionHistoryResponse)
def get_transaction_history(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> TransactionHistoryResponse:
    transactions = TransactionService.get_transaction_history(session, current_user.id)
    return TransactionHistoryResponse(items=[serialize_transaction(transaction) for transaction in transactions])


@router.get("/admin/history/predictions", response_model=HistoryResponse, tags=["admin"])
def get_admin_prediction_history(
    failed_only: bool = False,
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> HistoryResponse:
    tasks = PredictionService.get_all_prediction_history(session, failed_only=failed_only)
    return HistoryResponse(items=[serialize_prediction_history_item(task) for task in tasks])


@router.get("/admin/history/transactions", response_model=TransactionHistoryResponse, tags=["admin"])
def get_admin_transaction_history(
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> TransactionHistoryResponse:
    transactions = TransactionService.get_all_transaction_history(session)
    return TransactionHistoryResponse(items=[serialize_transaction(transaction) for transaction in transactions])
