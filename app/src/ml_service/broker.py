from __future__ import annotations

from time import sleep
from typing import Any

from ml_service.config import Settings, get_settings
from ml_service.schemas import PredictionTaskMessage


class RabbitMQPublishError(Exception):
    """Raised when a task cannot be published to RabbitMQ."""


def create_rabbitmq_connection(settings: Settings | None = None) -> Any:
    resolved_settings = settings or get_settings()

    import pika
    from pika.exceptions import AMQPError

    credentials = pika.PlainCredentials(
        resolved_settings.rabbitmq_user,
        resolved_settings.rabbitmq_password,
    )
    parameters = pika.ConnectionParameters(
        host=resolved_settings.rabbitmq_host,
        port=resolved_settings.rabbitmq_port,
        credentials=credentials,
    )

    last_error: Exception | None = None
    for _ in range(resolved_settings.rabbitmq_connection_attempts):
        try:
            return pika.BlockingConnection(parameters)
        except AMQPError as exc:
            last_error = exc
            sleep(resolved_settings.rabbitmq_connection_delay)

    if last_error is not None:
        raise last_error

    raise RabbitMQPublishError("RabbitMQ connection could not be established")


class RabbitMQTaskPublisher:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def publish(self, message: PredictionTaskMessage) -> None:
        try:
            import pika

            connection = create_rabbitmq_connection(self.settings)
            try:
                channel = connection.channel()
                channel.queue_declare(queue=self.settings.rabbitmq_queue, durable=True)
                channel.basic_publish(
                    exchange="",
                    routing_key=self.settings.rabbitmq_queue,
                    body=message.model_dump_json(),
                    properties=pika.BasicProperties(
                        content_type="application/json",
                        delivery_mode=2,
                    ),
                )
            finally:
                connection.close()
        except Exception as exc:  # pragma: no cover - depends on RabbitMQ runtime
            raise RabbitMQPublishError(
                f"Failed to publish task {message.task_id} to RabbitMQ"
            ) from exc
