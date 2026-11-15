# Set up an LLM

logpipe works without a model: the default `LLM_BACKEND=offline` uses rules. An LLM improves
four things:

| Where | What the model does |
|---|---|
| `calibrate` and the learning worker | writes the regex masks for new log formats |
| key naming | names the variable slots of a template (`user`, `latency_ms`, …) |
| alias checks | confirms that a reworded template is the same event (offline never re-associates) |
| Ask and root-cause analysis | writes the cited answer from the evidence logpipe collects |

## 1. Pick a provider and get a key

| Provider | `LLM_BACKEND` | Key variable | Default model | Get a key |
|---|---|---|---|---|
| Anthropic | `llm:anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5` | [console.anthropic.com](https://console.anthropic.com/) |
| OpenAI | `llm:openai` | `OPENAI_API_KEY` | `gpt-6-astra` | [platform.openai.com](https://platform.openai.com/api-keys) |
| Google Gemini | `llm:gemini` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `gemini-3.8-flash` | [aistudio.google.com](https://aistudio.google.com/apikey) |
| Groq | `llm:groq` | `GROQ_API_KEY` | `openai/gpt-oss-120b` | [console.groq.com](https://console.groq.com/keys) |

To pick another model, add it as a third part: `llm:anthropic:<model>`, `llm:openai:<model>`.

## 2. Put it in `.env`

```sh
LLM_BACKEND=llm:anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is read by `make` targets, by `logpipe start`, and by `docker compose` (the server
containers get `LLM_BACKEND` and all four key variables). If you run `uv run logpipe …` by hand,
export the variables first or use `uv run --env-file .env logpipe …`. `.env` is git-ignored;
don't commit keys.

You can also set the backend for one command, and that overrides `LLM_BACKEND`:

```sh
uv run --env-file .env logpipe calibrate --llm llm:openai
uv run --env-file .env logpipe server --llm llm:gemini:gemini-3.8-flash
make run LLM=llm:groq
```

`--llm` is on `calibrate`, `run`, `worker`, `server` and `eval`. `showcase` and `start` ask
which provider to use, and warn you if its key isn't set.

## 3. Check it

```sh
uv run --env-file .env logpipe calibrate --llm llm:anthropic --logs logs --sample 50
```

If the key is missing, it stops straight away with
`Provider 'anthropic' needs an API key; set ANTHROPIC_API_KEY.`. In the server, every LLM call
shows up on **Jobs** with its step log, and **Settings** shows which backend is running.

## Cost and limits

- The evidence sent per Ask or RCA call is capped by `LOGPIPE_ASK_BUDGET_TOKENS` (default
  6000), and one call times out after `LOGPIPE_ASK_LLM_TIMEOUT_SECONDS` (default 240).
- Masks are only requested for lines that no template matches, so after calibration most
  traffic never reaches the model.
- CI and the tests always use `offline`, so they make no paid calls.

## Other backends

- `hf`: a fine-tuned DeepSeek-R1-8B mask model that runs locally. It needs a GPU (see
  `notebooks/`).
- `llm:web:<site>`: your signed-in Gemini, Claude or ChatGPT tab, with no API key.
  See [Web chat as the LLM](web-chat.md).
