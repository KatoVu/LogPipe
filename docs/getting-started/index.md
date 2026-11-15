# Getting started

logpipe runs in three ways. Pick by what you want to see:

| You want to… | Run | Needs |
|---|---|---|
| Watch the product end to end: agent, review, alerts, Ask | `uv run logpipe showcase` | uv, Docker (runs the agent) |
| Ship your own hosts' logs and use the operator UI | `docker compose --profile platform up --build` | Docker |
| Parse and score a directory of log files in batch | `make calibrate train run` | uv |

Not sure? `uv run logpipe start` asks a few questions, starts the
services it needs (Kafka, ClickHouse) and runs the right command. It also prints that command,
so next time you can run it directly.

1. [Install](install.md): uv, Docker, `.env`, optional extras (Go, Node).
2. [Set up an LLM](llm.md): an API key for Anthropic, OpenAI, Gemini or Groq (optional).
3. [Set up the server](server.md): the shipped container or a source install.
4. [Run it](run.md): each mode step by step, with what you should see.
5. [Install the agent](agent.md): ship a host's logs, from a container, a release binary or a Go build.
6. [Commands](commands.md): every `logpipe` subcommand and `make` target.
