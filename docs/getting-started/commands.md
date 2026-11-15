# Commands

## `logpipe`

`uv run logpipe <command> …`. Every command has `-h`. `logpipe` on its own prints the list.

| Command | What it does | Main options |
|---|---|---|
| `start` | asks what to run, starts services, runs it | — |
| `server` | agent intake + operator UI + learning lifecycle + MCP | `--store`, `--queue`, `--llm`, `--data-dir`, `--epochs`, `--host`, `--port` |
| `showcase` | guided tour on a generated web shop with a real agent | `--no-ask`, `--auto` |
| `calibrate` | build registry v1 from historical logs, with a holdout-coverage report | `--logs`, `--registry`, `--llm`, `--sample`, `--depth`, `--similarity`, `--dataset` (GA/PA), `--tune` |
| `train` | train LogBERT on per-device windows | `--logs`, `--out`, `--window`, `--stride`, `--hidden`, `--layers`, `--heads`, `--epochs` |
| `run` | full pipeline: parse → store → enrich → detect | `--store`, `--queue`, `--llm`, `--model`, `--enrich-every`, `--no-enrich`, `--rehydrate`, `--window`, `--stride` |
| `worker` | learning worker on the Kafka unresolved queue | `--store`, `--llm`, `--batch`, `--poll`, `--max-attempts`, `--max-cycles` |
| `replay` | parse files with the frozen registry only, with no learning | `--logs`, `--registry` |
| `gen-iot` | write the synthetic IoT fleet (history / validation / test) | `--out`, `--devices`, `--history-hours`, `--test-hours`, `--seed` |
| `eval <name>` | benchmarks, their datasets, the report and the CI gate (below) | each has its own `-h` |

### `logpipe eval`

Every benchmark writes into `--out` (default `artifacts/eval`), where `report` and `gate` read.

| Subcommand | What it does | Main options |
|---|---|---|
| `parsing` | GA/PA on Loghub-2k vs. DeepParse | `--llm`, `--no-tune` |
| `alias` | alias decisions on labelled template pairs | — |
| `iot` | synthetic fleet end to end, with ablations | `--llm`, `--devices`, `--test-hours`, `--epochs`, `--threads` |
| `logbert` | LogBERT on an HDFS or BGL subset | `--dataset`, `--normal`, `--abnormal`, `--max-lines`, `--epochs` |
| `rca` | root cause on 15 faults injected into a generated web shop | `--llm`, `--record f.json`, `--replay f.json`, `--modes` |
| `rcaeval` | root cause on RCAEval cases | `TASKS_DIR`, `--via direct\|mcp`, `--llm`, `--limit`, `--fresh` |
| `throughput` | how many lines/s one server sustains, with CPU and memory | `--store`, `--max-rate`, `--step-seconds`, `--out` |
| `report` | summarise `--out` into `REPORT.md` | — |
| `gate` | compare `--out` with `eval/baseline.json` (the nightly CI check) | `--baseline`, `--write-baseline` |
| `fetch` | download the datasets up front | `loghub2k`, `HDFS`, `BGL` |

## `make`

`make help` lists every target. Make variables override for one run: `LLM=…`, `EPOCHS=…`,
`THREADS=…`, `STORE=…`, `EVAL_OUT=…`, `DATASET=…`, `VIA=…`, `LIMIT=…`,
`VARIANTS=…`, `FRESH=1`.

=== "Setup & services"

    | target | |
    |---|---|
    | `setup` | `uv sync --group dev`, create `.env` |
    | `start` | interactive start |
    | `docker-up` / `docker-down` | ClickHouse + Kafka |
    | `clean` | caches only (keeps datasets, models, results) |

=== "Pipeline"

    | target | |
    |---|---|
    | `calibrate` | registry v1 from `LOGS_PATH` |
    | `train` | LogBERT on `LOGS_PATH` |
    | `run` | full pipeline |
    | `run-fast` | fast path only, unknown lines to Kafka |
    | `worker` | learning worker on Kafka |
    | `replay` | frozen registry only |
    | `gen-iot` / `fleet` | synthetic fleet / end to end on it |

=== "Evaluation"

    | target | |
    |---|---|
    | `data`, `data-loghub`, `data-hdfs`, `data-bgl` | datasets |
    | `eval` | parsing, alias, IoT, HDFS, BGL, then the report |
    | `eval-parsing` / `eval-alias` / `eval-iot` / `eval-hdfs` / `eval-bgl` | one benchmark |
    | `eval-rcaeval` | RCAEval: `DATASET`, `VIA=direct\|mcp`, `LLM`, `LIMIT`, `VARIANTS`, `FRESH=1` |
    | `eval-report` / `eval-gate` / `eval-baseline` | report, CI gate, record baseline |
    | `bench` | throughput (`STORE=clickhouse` to include writes) |
    | `retrain-data` / `retrain-eval` | mask-model fine-tune data and scoring |

=== "Quality"

    | target | |
    |---|---|
    | `test` | all but integration |
    | `test-fast` | no model training, no integration |
    | `test-slow` | only model-training tests |
    | `test-integration` | ClickHouse + Kafka (starts them) |
    | `lint` / `format` / `typecheck` / `check` | ruff, ruff --fix, pyright, all |
    | `docs` / `docs-build` | serve / build this site |
