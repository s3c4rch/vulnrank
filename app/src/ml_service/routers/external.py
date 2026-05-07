from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ml_service.database import get_db_session
from ml_service.dependencies import get_current_user
from ml_service.models import User
from ml_service.schemas import OpenAICredentialRequest, OpenAICredentialView
from ml_service.serializers import serialize_openai_credential
from ml_service.services import ExternalModelCredentialService


router = APIRouter(prefix="/external-credentials", tags=["external-models"])


@router.get("/openai", response_model=OpenAICredentialView)
def get_openai_credential(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> OpenAICredentialView:
    credential = ExternalModelCredentialService.get_openai_credential(session, current_user.id)
    return serialize_openai_credential(credential)


@router.put("/openai", response_model=OpenAICredentialView)
def upsert_openai_credential(
    payload: OpenAICredentialRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> OpenAICredentialView:
    credential = ExternalModelCredentialService.upsert_openai_credential(
        session,
        user_id=current_user.id,
        api_key=payload.api_key,
        model_name=payload.model_name,
    )
    return serialize_openai_credential(credential)


@router.delete("/openai", status_code=status.HTTP_204_NO_CONTENT)
def delete_openai_credential(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> None:
    ExternalModelCredentialService.disable_openai_credential(session, current_user.id)
