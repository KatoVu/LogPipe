# Run it

## 1. Interactive start

```sh
uv run logpipe start      # or `make start`
```

It asks what to run (your own logs, or the server for agents), which queue
(in-memory, or Kafka, which it starts in Docker for you), who names keys and confirms
re-associations (offline rules or a [hosted API](llm.md)), and the few settings
that matter. Then it prints the equivalent commands and runs them. Answers are saved in
`.logpipe/start.json` and become next time's defaults.

## 2. Showcase: the product, guided

```sh
uv run logpipe showcase           # asks store and LLM; --no-ask for memory + offline
uv run logpipe showcase --auto    # does every act itself
```

It starts the server and a generated web shop's logs (nginx, a Java API with stack traces,
Postgres). Then it walks through eight acts: start a real `logpipe-agent` container and track the
files (it prints the `docker run` and `track` commands for you to run), draft templates, review
them and write a parsing rule,
calibrate and train, explore, a payment-gateway outage that LogBERT flags, and a new service
whose templates are drafted for review automatically. Each act waits for you to do it in the
UI; Enter lets it do the act instead. Files go to `artifacts/showcase/`.

## 3. The platform: agents + server + UI

```sh
cp .env.example .env
docker compose --profile platform up --build
# open http://localhost:8080 and log in with changeme; it asks for a new password
```

Only want the server, from the shipped image or from source? See [Set up the server](server.md).

The `platform` profile runs ClickHouse, the server, a log generator and an agent. Follow the
setup guide in the UI: API key → install agents → track files → sample size → streams → review
templates → train. `--profile platform-mongo` runs the same on MongoDB.

Without Docker:

```sh
uv run logpipe server --store memory           # API + UI on :8080
cd web && npm ci && npm run dev                # UI dev server on :5173, proxies /api
cd agent && go build -o logpipe-agent . && ./logpipe-agent -h
```

Install an agent on a real host: [Install the agent](agent.md) (container, release binary via `install.sh`, or
a Go build; no root needed). Anything that speaks OTLP/HTTP logs can send to `POST /v1/logs` too.
[Server and learning lifecycle →](../agent-platform.md)

## 4. Batch: a directory of log files

```sh
# LOGS_PATH in .env; each file is one device
make calibrate      # masks + templates + key names from history → registry v1 (+ .report.json)
make train          # LogBERT on per-device windows of the history
make run            # parse, store, learn PENDING lines, detect
```

End to end on the synthetic fleet:

```sh
uv run logpipe gen-iot --out artifacts/fleet
uv run logpipe calibrate --logs artifacts/fleet/history
uv run logpipe train --logs artifacts/fleet/history
uv run logpipe run --logs artifacts/fleet/test             # --store clickhouse after make docker-up
```

### With Kafka: a fast path and separate learning workers

```sh
make docker-up
uv run logpipe worker --store memory --llm offline         # 1+ terminals
uv run logpipe run --queue kafka --no-enrich --logs artifacts/fleet/test --store memory
```

[Unresolved queue on Kafka →](../kafka.md)

## 5. Connect an AI assistant (MCP)

1. In the UI, **Settings → API keys**, create a key with use **AI assistant**.
2. Add it to Claude Code:

   ```sh
   claude mcp add --transport http logpipe https://<your-server>/mcp \
     --header "Authorization: Bearer lp_..."
   ```

3. Ask "investigate the latest open incident", or use the `investigate_incident` /
   `review_batch` prompts. [MCP →](../architecture/mcp.md)

## 6. Benchmarks

```sh
make eval                                            # parsing, alias, IoT, HDFS, BGL + report
make eval-rcaeval DATASET=re3-ob                     # RCAEval, rules only
make eval-rcaeval DATASET=re3-ob LLM=llm:anthropic   # + an LLM synthesizer
make eval-rcaeval DATASET=re3-ob VIA=mcp             # Claude Code over /mcp
uv run logpipe eval throughput                       # throughput of one server
```

[Benchmarks →](../benchmarks/index.md)
