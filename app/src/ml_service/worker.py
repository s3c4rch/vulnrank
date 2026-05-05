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
from ml_service.schemas import PredictionTaskMessage
from ml_service.services import EntityNotFoundError, PredictionService


LOGGER = logging.getLogger(__name__)


def process_delivery(
    body: bytes,
    session_factory: sessionmaker,
    worker_id: str,
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

    try:
        message = PredictionTaskMessage.model_validate(raw_payload)
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

    with session_factory() as session:
        try:
            task = PredictionService.process_task(
                session,
                task_id=message.task_id,
                model_name=message.model,
                features=message.features,
                worker_id=worker_id,
            )
        except Exception as exc:
            session.rollback()
            PredictionService.fail_task(session, message.task_id, str(exc))
            LOGGER.warning("Worker %s failed task %s: %s", worker_id, message.task_id, exc)
            return {
                "task_id": message.task_id,
                "prediction": None,
                "worker_id": worker_id,
                "status": "failed",
            }
        prediction_value = task.result.prediction_value if task.result else None

    return {
        "task_id": message.task_id,
        "prediction": prediction_value,
        "worker_id": worker_id,
        "status": "success",
    }


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
