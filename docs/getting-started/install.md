# Install

## Requirements

| Tool | Version | Needed for |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | recent | everything in Python (it installs Python 3.13 itself) |
| Docker + Compose | recent | ClickHouse, MongoDB, Kafka, the platform profile, the showcase's agent |
| Go | 1.23+ | building `logpipe-agent` from `agent/` |
| Node | 22+ | developing the web UI in `web/` (the Docker image builds it for you) |
| A GPU | — | only for `LLM_BACKEND=hf` (the fine-tuned mask model) |

CPU is enough for everything else, including LogBERT training at the eval sizes.

## Python environment

```sh
git clone git@github.com:KatoVu/LogPipe.git
cd LogPipe
make setup          # = uv sync --group dev, and creates .env from .env.example
```

`DeepParse/` is a second Python package in the same repo (mask synthesis, the Drain engine, chat
providers). It's installed into the same environment as
`deepparse-artifact`, so nothing extra is needed.

## `.env`

`.env.example` lists what most installs change; every other setting has a default in
`logpipe/settings.py` and can be overridden by its prefix (see [Configuration](../guides/configuration.md)).

| Variable | Default | What it does |
|---|---|---|
| `LOGS_PATH` | `logs` | directory replayed by `calibrate` / `run` (one file = one device) |
| `REGISTRY_PATH` | `artifacts/registry.json` | template registry written by `calibrate` |
| `LLM_BACKEND` | `offline` | `offline`, `hf` or `llm:<provider>[:<model>]` ([Set up an LLM](llm.md)) |
| `QUEUE` | `memory` | unresolved queue: `memory` or `kafka` |
| `CLICKHOUSE_*` | see file | store connection, plus a read-only user for Query/Ask |
| `MONGO_*` | see file | store connection, plus a read-only URI for free-form queries |
| `KAFKA_*` | see file | broker, topic, consumer group, partition key |
| `LOGPIPE_ADMIN_PASSWORD` | unset | UI password; unset = `changeme`, replaced at the first login |

## Services

```sh
make docker-up                      # ClickHouse + Kafka, waits until healthy
docker compose --profile mongo up -d mongo   # MongoDB instead of ClickHouse
make docker-down
```

The memory store and memory queue need nothing running, and all unit tests use them.

## Optional: an LLM

Offline rules work out of the box. For better masks, key names and Ask answers, add an API
key: [Set up an LLM](llm.md).

## Check the install

```sh
make test-fast                   # unit tests without model training
uv run logpipe showcase --no-ask  # the product end to end, memory store and offline LLM
```
