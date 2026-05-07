# vulnrank

`vulnrank` is a training ML service for prioritizing vulnerability scan
findings. It provides a Web cabinet and REST API with moderated top-ups,
scanner file upload, asynchronous batch prediction, history, and admin review.

Repository: https://github.com/s3c4rch/vulnrank

## Project

- users upload JSON, CSV, or ZIP scanner outputs;
- valid findings go through async prediction via RabbitMQ and `ml-runtime`;
- invalid records are returned immediately;
- credits are charged only for successfully processed records;
- completed tasks can be opened or downloaded as standalone HTML reports.

## Architecture

The Docker stack contains:

- `app` — FastAPI backend, REST API, Web UI, database initialization;
- `web-proxy` — Nginx reverse proxy on `80` and `443`;
- `database` — PostgreSQL runtime database;
- `rabbitmq` — message broker and management UI on `15672`;
- `ml-runtime` — Ollama runtime on `11434`;
- `worker-1`, `worker-2` — async consumers that process prediction tasks.

The main flow is:

`Web UI / REST API -> FastAPI -> RabbitMQ -> workers -> ml-runtime -> PostgreSQL`

## Run

Requirements:

- Docker
- Docker Compose v2

Prepare local runtime files:

```bash
cp app/.env.example app/.env
cp app/Dockerfile.example app/Dockerfile
cp worker/Dockerfile.example worker/Dockerfile
cp web-proxy/Dockerfile.example web-proxy/Dockerfile
cp web-proxy/nginx.conf.example web-proxy/nginx.conf
cp docker-compose.yml.example docker-compose.yml
```

Start the stack:

```bash
docker compose up --build
```

If needed, pull a local Ollama model first:

```bash
docker compose up -d ml-runtime
docker compose exec ml-runtime ollama pull gemma3:4b
```

Open:

- Web UI: http://localhost/
- API docs: http://localhost/docs
- Health check: http://localhost/health
- RabbitMQ UI: http://localhost:15672

Demo accounts:

| Role | Email | Password |
| --- | --- | --- |
| User | `demo-user@example.com` | `demo-user-password` |
| Admin | `demo-admin@example.com` | `demo-admin-password` |

Useful commands:

```bash
docker compose run --rm app pytest -q
docker compose down
docker compose down -v
```
