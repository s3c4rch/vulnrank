import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
APP_NAME = os.getenv("APP_NAME", "vulnrank")
APP_ENV = os.getenv("APP_ENV", "development")


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
                },
            )
            return

        self._send_json(
            200,
            {
                "message": "service is running",
                "service": APP_NAME,
                "environment": APP_ENV,
            },
        )

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    server = HTTPServer((APP_HOST, APP_PORT), RequestHandler)
    print(f"Starting {APP_NAME} on {APP_HOST}:{APP_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
