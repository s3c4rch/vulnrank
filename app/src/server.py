import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from ml_service.config import get_settings
from ml_service.database import get_engine, wait_for_database
from ml_service.init_db import initialize_database

SETTINGS = get_settings()
APP_HOST = SETTINGS.app_host
APP_PORT = SETTINGS.app_port
APP_NAME = SETTINGS.app_name
APP_ENV = SETTINGS.app_env


class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": APP_NAME,
                    "environment": APP_ENV,
                    "database": "initialized",
                },
            )
            return

        self._send_json(
            200,
            {
                "message": "ML service app stub is running",
                "service": APP_NAME,
                "environment": APP_ENV,
            },
        )

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    engine = get_engine()
    wait_for_database(engine)
    initialize_database(engine=engine)

    server = HTTPServer((APP_HOST, APP_PORT), RequestHandler)
    print(f"Starting {APP_NAME} on {APP_HOST}:{APP_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
