from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from ml_service.broker import create_rabbitmq_connection
from ml_service.config import get_settings
from ml_service.database import get_engine, get_session_factory, wait_for_database
from ml_service.inference import warm_up_model
from ml_service.init_db import initialize_database
from ml_service.model_catalog import is_local_demo_model, is_openai_provider_model
from ml_service.model_runtime import (
    ModelRuntimeClient,
    ModelRuntimeError,
    ModelRuntimePrediction,
    OllamaModelRuntimeClient,
    OpenAIModelRuntimeClient,
)
from ml_service.schemas import PredictionTaskMessage, ScanUploadTaskMessage
from ml_service.services import EntityNotFoundError, ExternalModelCredentialService, PredictionService


LOGGER = logging.getLogger(__name__)


def process_delivery(
    body: bytes,
    session_factory: sessionmaker,
    worker_id: str,
    runtime_client: ModelRuntimeClient | None = None,
) -> dict[str, Any]:
    task_id: str | None = None

    try:
        raw_payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        LOGGER.warning("Worker %s received invalid JSON payload: %s", worker_id, exc)
        return {
            "task_id": None,
            "prediction": None,
            "worker_id": worker_id,
            "status": "failed",
        }

    if isinstance(raw_payload, dict):
        task_id = raw_payload.get("task_id")

    is_scan_upload = isinstance(raw_payload, dict) and raw_payload.get("message_type") == "scan_upload"
    message_model = ScanUploadTaskMessage if is_scan_upload else PredictionTaskMessage

    try:
        message = message_model.model_validate(raw_payload)
    except ValidationError as exc:
        if task_id:
            with session_factory() as session:
                try:
                    PredictionService.fail_task(session, task_id, str(exc))
                except EntityNotFoundError:
                    LOGGER.warning("Worker %s could not mark unknown task %s as failed", worker_id, task_id)
        LOGGER.warning("Worker %s rejected invalid task %s: %s", worker_id, task_id, exc)
        return {
            "task_id": task_id,
            "prediction": None,
            "worker_id": worker_id,
            "status": "failed",
        }

    if isinstance(message, ScanUploadTaskMessage):
        return _process_scan_upload_message(
            message=message,
            session_factory=session_factory,
            worker_id=worker_id,
            runtime_client=runtime_client,
        )

    return _process_prediction_message(
        message=message,
        session_factory=session_factory,
        worker_id=worker_id,
        runtime_client=runtime_client,
    )


def _process_prediction_message(
    message: PredictionTaskMessage,
    session_factory: sessionmaker,
    worker_id: str,
    runtime_client: ModelRuntimeClient | None = None,
) -> dict[str, Any]:
    try:
        with session_factory() as session:
            processing_context = PredictionService.start_task_processing(
                session,
                task_id=message.task_id,
                model_name=message.model,
                features=message.features,
                worker_id=worker_id,
            )
            if processing_context.already_completed:
                return {
                    "task_id": message.task_id,
                    "prediction": processing_context.existing_prediction_value,
                    "worker_id": worker_id,
                    "status": "success",
                }

        if is_local_demo_model(processing_context.model_name):
            prediction_value, predicted_priority, confidence = PredictionService.build_model_inference(
                processing_context.features
            )
        else:
            runtime_prediction = _predict_with_runtime(
                session_factory=session_factory,
                user_id=processing_context.user_id,
                model_name=processing_context.model_name,
                features=processing_context.features,
                runtime_client=runtime_client,
            )
            predicted_priority = runtime_prediction.predicted_priority
            confidence = runtime_prediction.confidence
            prediction_value = PredictionService.prediction_value_for_priority(predicted_priority)

        with session_factory() as session:
            task = PredictionService.complete_task(
                session,
                task_id=message.task_id,
                predicted_priority=predicted_priority,
                confidence=confidence,
                worker_id=worker_id,
                prediction_value=prediction_value,
            )
            prediction_value = task.result.prediction_value if task.result else None
    except Exception as exc:
        with session_factory() as session:
            session.rollback()
            try:
                PredictionService.fail_task(session, message.task_id, str(exc))
            except EntityNotFoundError:
                LOGGER.warning("Worker %s could not mark unknown task %s as failed", worker_id, message.task_id)
        LOGGER.warning("Worker %s failed task %s: %s", worker_id, message.task_id, exc)
        return {
            "task_id": message.task_id,
            "prediction": None,
            "worker_id": worker_id,
            "status": "failed",
        }

    return {
        "task_id": message.task_id,
        "prediction": prediction_value,
        "worker_id": worker_id,
        "status": "success",
    }


def _process_scan_upload_message(
    message: ScanUploadTaskMessage,
    session_factory: sessionmaker,
    worker_id: str,
    runtime_client: ModelRuntimeClient | None = None,
) -> dict[str, Any]:
    processed_predictions: list[dict[str, Any]] = []
    rejected_count = 0

    try:
        with session_factory() as session:
            processing_context = PredictionService.start_batch_task_processing(
                session,
                task_id=message.task_id,
                model_name=message.model,
                records=message.records,
                worker_id=worker_id,
            )
            if processing_context.already_completed:
                return {
                    "task_id": message.task_id,
                    "prediction": processing_context.existing_prediction_value,
                    "worker_id": worker_id,
                    "status": "success",
                    "processed_count": processing_context.existing_processed_count,
                    "rejected_count": processing_context.existing_rejected_count,
                }
            task = PredictionService.get_task(session, message.task_id)
            payload = task.input_payload[0] if task.input_payload else {}
            if isinstance(payload, dict):
                rejected_count = int(payload.get("rejected_count") or 0)

        for record in message.records:
            runtime_prediction = _predict_with_runtime(
                session_factory=session_factory,
                user_id=processing_context.user_id,
                model_name=processing_context.model_name,
                features=record,
                runtime_client=runtime_client,
            )
            processed_predictions.append(
                {
                    "record_index": int(record.get("record_index", len(processed_predictions))),
                    "finding_type": record.get("finding_type"),
                    "predicted_priority": runtime_prediction.predicted_priority.value,
                    "confidence": runtime_prediction.confidence,
                    "reason": runtime_prediction.reason,
                }
            )

        with session_factory() as session:
            task = PredictionService.complete_batch_task(
                session,
                task_id=message.task_id,
                processed_predictions=processed_predictions,
                rejected_count=rejected_count,
                worker_id=worker_id,
            )
            prediction_value = task.result.prediction_value if task.result else None
            processed_count = task.result.processed_count if task.result else 0
            rejected_count = task.result.rejected_count if task.result else rejected_count
    except Exception as exc:
        with session_factory() as session:
            session.rollback()
            try:
                if processed_predictions:
                    unprocessed_count = max(len(message.records) - len(processed_predictions), 0)
                    PredictionService.complete_batch_task(
                        session,
                        task_id=message.task_id,
                        processed_predictions=processed_predictions,
                        rejected_count=rejected_count + unprocessed_count,
                        worker_id=worker_id,
                        error_message=f"Batch stopped after partial processing: {exc}",
                    )
                else:
                    PredictionService.fail_task(session, message.task_id, str(exc))
            except EntityNotFoundError:
                LOGGER.warning("Worker %s could not mark unknown task %s as failed", worker_id, message.task_id)
            except Exception as final_exc:
                session.rollback()
                try:
                    PredictionService.fail_task(session, message.task_id, str(final_exc))
                except EntityNotFoundError:
                    LOGGER.warning("Worker %s could not mark unknown task %s as failed", worker_id, message.task_id)
        LOGGER.warning("Worker %s failed scan upload task %s: %s", worker_id, message.task_id, exc)
        return {
            "task_id": message.task_id,
            "prediction": None,
            "worker_id": worker_id,
            "status": "failed",
            "processed_count": len(processed_predictions),
            "rejected_count": rejected_count,
        }

    return {
        "task_id": message.task_id,
        "prediction": prediction_value,
        "worker_id": worker_id,
        "status": "success",
        "processed_count": processed_count,
        "rejected_count": rejected_count,
    }


def _predict_with_runtime(
    *,
    session_factory: sessionmaker,
    user_id: str,
    model_name: str,
    features: dict[str, Any],
    runtime_client: ModelRuntimeClient | None = None,
) -> ModelRuntimePrediction:
    if is_openai_provider_model(model_name):
        with session_factory() as session:
            credential = ExternalModelCredentialService.get_openai_credential(session, user_id)
            if credential is None:
                raise ModelRuntimeError("OpenAI credentials are not configured for this user")
            external_model_name = credential.model_name
            api_key = credential.api_key

        if runtime_client is not None:
            return runtime_client.predict_priority(model_tag=external_model_name, features=features)
        return OpenAIModelRuntimeClient(api_key=api_key).predict_priority(
            model_tag=external_model_name,
            features=features,
        )

    resolved_runtime_client = runtime_client or OllamaModelRuntimeClient()
    return resolved_runtime_client.predict_priority(
        model_tag=model_name,
        features=features,
    )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    engine = get_engine()
    wait_for_database(engine)
    initialize_database(engine=engine)
    warm_up_model()
    session_factory = get_session_factory()

    connection = create_rabbitmq_connection(settings)
    channel = connection.channel()
    channel.queue_declare(queue=settings.rabbitmq_queue, durable=True)
    channel.basic_qos(prefetch_count=1)

    LOGGER.info("Worker %s is waiting for tasks on queue %s", settings.worker_id, settings.rabbitmq_queue)

    def callback(channel, method, _, body: bytes) -> None:
        result = process_delivery(
            body=body,
            session_factory=session_factory,
            worker_id=settings.worker_id,
        )
        LOGGER.info("Worker result: %s", result)
        channel.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(
        queue=settings.rabbitmq_queue,
        on_message_callback=callback,
    )
    channel.start_consuming()


if __name__ == "__main__":
    main()
