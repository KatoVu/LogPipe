# Web chat as the LLM

Instead of an API key, logpipe can drive your own signed-in Gemini, Claude or ChatGPT tab in a
real browser (Playwright, through `deepparse.ai_scraper`). It suits experiments and
benchmarks when you don't want to pay for an API. For anything you run unattended, use a
[hosted API](llm.md): the chat site picks the model and the sampling, so runs aren't
reproducible, and a site redesign can break it until its selectors are updated.

| Site | `LLM_BACKEND` | Sign-in |
|---|---|---|
| Gemini | `llm:web:gemini` | optional, it answers signed out |
| Claude | `llm:web:claude` | needed once |
| ChatGPT | `llm:web:chatgpt` | needed once |

## 1. Get a browser

Two browsers work: `camoufox` (a bundled Firefox build, the default) or `chrome` (the
`chromium` on your `PATH`).

```sh
uv run python -m camoufox fetch          # once, and again after camoufox upgrades
```

With Chromium, there's nothing to download.

## 2. Sign in, check, ask

The scraper is a Python module with three commands:

```sh
uv run python -m deepparse.ai_scraper login claude              # visible window, sign in, profile saved
uv run python -m deepparse.ai_scraper check claude              # headless; exit 0 if the login still works
uv run python -m deepparse.ai_scraper ask claude "Reply with OK."   # prints the reply
```

Each command takes `--browser camoufox|chrome` (default `camoufox`) and `--chrome-path`
(default `chromium`). `ask` also takes `--headed` and `--timeout`. A profile belongs to one
browser, so sign in again if you switch browsers. Cookie banners are answered with
**Reject all**.

## 3. Use it from logpipe

```sh
WEB_BROWSER=chromium uv run logpipe server --llm llm:web:gemini
WEB_BROWSER=camoufox uv run logpipe calibrate --llm llm:web:claude
```

Or set both once in `.env`:

```sh
LLM_BACKEND=llm:web:gemini
WEB_BROWSER=chromium        # or camoufox
```

## From Python

```python
from deepparse.ai_scraper.session import WebChatSession
from deepparse.ai_scraper.sites import SITES

with WebChatSession(SITES["gemini"], browser="chrome", headless=True) as chat:
    print(chat.ask("Reply with OK.").text)
```

Through logpipe's adapter (the same thing `--llm` builds):

```python
from logpipe.enrich.llm import get_adapter

adapter = get_adapter("llm:web:gemini", browser="chrome")
```

## Where it writes

Everything goes under `DeepParse/.webchat/`, or `$DEEPPARSE_WEBCHAT_DIR` if you set it:

| Path | What |
|---|---|
| `profiles/<browser>/` | saved logins |
| `conversations/<site>.jsonl` | prompts and replies |
| `errors/<time>_<site>_<Error>/` | `page.html`, `screenshot.png` and `error.json` from a failed run |

## When a site changes

Sites are data, in `DeepParse/deepparse/ai_scraper/sites.py`. When a site redesigns, add a
selector at the **front** of the matching tuple; no code change is needed. Look at the
`errors/` capture to see the new page, then confirm the fix with `check` and `ask`. To add a
site, add a `ChatSite` entry:

```python
"mychat": ChatSite(
    name="MyChat",
    url="https://mychat.example/new",                 # opening it starts a fresh chat
    composer=("div[contenteditable='true']",),         # most specific first
    send_button=("button[aria-label='Send']",),
    generating=("button[aria-label='Stop']",),         # present only while streaming
    responses=("div.assistant-message",),              # one per reply, in order
    login_urls=("/login",),
    logged_out=("a[href*='login']",),
    error_banners=("[role='alert']",),
    reject_cookies=("button:has-text('Reject all')",), # answered automatically
    more_cookie_options=(),                            # what reveals a hidden reject button
),
```
