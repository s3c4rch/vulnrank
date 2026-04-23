import uvicorn

from ml_service.api import create_app
from ml_service.config import get_settings

SETTINGS = get_settings()
APP_HOST = SETTINGS.app_host
APP_PORT = SETTINGS.app_port
app = create_app()

def main() -> None:
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)


if __name__ == "__main__":
    main()
