# logpipe-agent

A small Go collector (one static binary, ~15 MB image) that tails logs on a host and ships them
to `logpipe server`. Config follows the Datadog Agent layout: one `agent.yaml` plus one file per
integration in `conf.d/`.

## Quick start (docker)

Create an API key in the UI (**Settings → API keys**), put it and your server URL in, and start it:

```bash
docker run -d --name logpipe-agent --restart unless-stopped \
  -e LOGPIPE_ENDPOINT=http://logpipe.example:8080 \
  -e LOGPIPE_API_KEY=lp_xxx \
  -e LOGPIPE_HOSTNAME=$(hostname) \
  -v /var/log:/var/log:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v logpipe-agent:/var/lib/logpipe-agent \
  -v "$PWD/conf.d:/etc/logpipe-agent/conf.d" \
  ghcr.io/katovu/logpipe-agent:latest
```

`-d --restart unless-stopped` keeps it running across crashes and reboots. `/var/log` is what it
can tail, the Docker socket gives it other containers' logs, the `logpipe-agent` volume keeps
offsets and the send buffer, and `conf.d` keeps the sources you add with `track`.

The agent appears on the **Agents** page as soon as it connects. Add sources there (pushed to the
agent as `conf.d/remote.yaml`), or from the shell:

```bash
alias lpa='docker exec logpipe-agent logpipe-agent'
lpa track '/var/log/myapp/*.log' --service myapp --source java
lpa status
docker logs -f logpipe-agent
```

Upgrade with `docker pull ghcr.io/katovu/logpipe-agent:latest && docker rm -f logpipe-agent`, then
the same `docker run`; the volume keeps its place in every file.

## Quick start (binary)

Every release has binaries for Linux (amd64, arm64, armv7) and macOS (amd64, arm64). The installer
picks yours, checks its sha256 and puts it in `~/.local/bin` (`/usr/local/bin` as root):

```bash
curl -fsSL https://github.com/KatoVu/LogPipe/releases/latest/download/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"   # where it goes without root; add this to your shell profile
```

Then set it up and start it in the background:

```bash
logpipe-agent init --endpoint http://logpipe.example:8080 --api-key lp_xxx
logpipe-agent track '/var/log/myapp/*.log' --service myapp --source java --auto-multiline
logpipe-agent check
logpipe-agent service install   # or `run` to stay in the foreground
```

`LOGPIPE_AGENT_VERSION=v1.0.1` pins a release, `LOGPIPE_AGENT_INSTALL_DIR` changes where it goes,
and `LOGPIPE_AGENT_BASE_URL` points at a mirror holding the release files. Without the script,
download `logpipe-agent_<os>_<arch>.tar.gz` and `checksums.txt` from the
[releases page](https://github.com/KatoVu/LogPipe/releases). From source: `cd agent && go build -o logpipe-agent .`

To keep it running in the background, `logpipe-agent service install` writes and starts a systemd
user unit (Linux) or a launchd agent (macOS) pointing at this binary and config directory; as root it
installs a system-wide one. On Linux as a normal user, also run `loginctl enable-linger $USER` so it
keeps running after you log out. `service uninstall` removes it. The key and endpoint must be written in
`agent.yaml` (not `${VAR}`), because the service doesn't get your shell's environment.

No sudo needed. As a normal user the config lives in `~/.config/logpipe-agent` (offsets and the
send buffer in its `run/`); as root it is `/etc/logpipe-agent`. `-c DIR` (or
`$LOGPIPE_AGENT_CONFIG`) points at another directory.

The agent only needs to *read* the files it tails. Most of `/var/log` is readable by the `adm`
group, so add yourself once instead of running the agent as root:
`sudo usermod -aG adm $USER` (log out and back in). `check` names any file it can't read.

## CLI

| Command | What it does |
|---|---|
| `init --endpoint URL [--api-key KEY] [--hostname H]` | write `agent.yaml` |
| `run` | run in the foreground; reloads by itself when any config file changes |
| `service install` / `uninstall` | run it in the background at boot: systemd unit (user, or system as root) or launchd agent |
| `track TARGET [flags]` | add a source to `conf.d/<service>.yaml` |
| `untrack TARGET` | remove it (sources pushed from the UI are removed in the UI) |
| `list` | every source and its rules |
| `config get KEY` / `config set KEY VALUE` | read or change a key in `agent.yaml`, keeping comments |
| `config edit [FILE]` | open `agent.yaml` (or `conf.d/FILE.yaml`) in `$EDITOR`; saved only if it validates |
| `config show` | effective config, API key redacted |
| `status` | what the running agent is doing: files, line counts, send errors, buffer size |
| `check` | validate config, count matching files, test the server and API key |

`TARGET` is a file glob, `journald` or `journald:UNIT`, `docker`, `tcp:PORT` or `udp:PORT`.

`track` flags: `--service`, `--source`, `--tag k:v` (repeatable), `--name FILE`,
`--multiline REGEX`, `--auto-multiline`, `--exclude REGEX`, `--include REGEX`,
`--mask 'REGEX=>REPLACEMENT'`, `--label key=value` (docker), `--start beginning|end`.

## Config reference

`agent.yaml`:

```yaml
api_key: ${LOGPIPE_API_KEY}     # ${VAR} is read from the environment
endpoint: https://logpipe.example:8080
hostname: gw-01                 # default: the system hostname
tags: [env:prod, site:hanoi]    # added to every event
run_path: /var/lib/logpipe-agent  # offsets + send buffer (default: <config dir>/run)
buffer_mb: 100                  # disk buffer while the server is down; oldest batches dropped first
remote_config: true             # let the UI push sources into conf.d/remote.yaml (default true)
auto_multi_line_detection: false  # default for sources without a multi_line rule
```

`LOGPIPE_API_KEY`, `LOGPIPE_ENDPOINT` and `LOGPIPE_HOSTNAME` override the file.

`conf.d/<name>.yaml`:

```yaml
logs:
  - type: file                  # file | journald | docker | tcp | udp
    path: /var/log/myapp/*.log  # file: glob
    service: myapp
    source: java
    tags: [team:payments]
    start_position: end         # end (default) or beginning, for files seen the first time
    ignore_older_than: 24h      # release a file once a newer one in its series exists and it is this quiet; 0 = never
    log_processing_rules:
      - type: multi_line        # lines not matching start a continuation of the previous event
        name: new_event
        pattern: '^\d{4}-\d{2}-\d{2}'
      - type: exclude_at_match
        pattern: healthcheck
      - type: include_at_match
        pattern: 'ERROR|WARN'
      - type: mask_sequences
        pattern: 'password=\S+'
        replace_placeholder: 'password=[REDACTED]'
  - type: journald
    unit: nginx.service         # empty: every unit
  - type: docker
    container_labels: [logs=true]  # empty: every container
  - type: udp                   # syslog listener, RFC 3164/5424
    port: 5514
```

## How it behaves

- **Multiline**: with a `multi_line` rule, a line that does not match the pattern joins the
  previous event (Java stack traces, Python tracebacks). `auto_multi_line_detection` picks the
  pattern from the first line that starts with a known timestamp/level format. An event is sent
  when the next one starts or after 1 s of quiet, and is capped at 256 KB.
- **Rules** other than `multi_line` run in order on the whole event.
- **Files**: globs are re-checked every 250 ms; rotation (rename + new file) and copytruncate are
  both followed. Offsets are saved to `run/positions.json`, so a restart neither resends nor
  skips lines (a half-written last line is re-read, never split).
- **File identity**: a file is its device + inode + a hash of its first 1 KB, not its name. A file
  renamed inside the glob (`app.log` -> `app.log.1`, even while the agent is stopped) keeps its
  offset instead of being read again, and a new file that reuses a deleted file's inode is read
  from its start.
- **Log series**: `app.log`, `app.log.3`, `app.001.log`, `app-2026-11-17.log` and `app.log.1.gz`
  are one series, `app`, which is also the service when `service:` is unset. Once a newer file of
  the series exists and an older one has been quiet for `ignore_older_than` (default 24h), the old
  one is drained and closed and listed under `skipped_files` in `status`. It is still checked: a
  write to it reopens it at its offset. A quiet file with no newer sibling (`error.log`) is never
  released. On the first start, released files are not replayed even with
  `start_position: beginning`; set `ignore_older_than: 0` to backfill an archive.
- **Shipping**: batches of up to 500 events or 1 s as **OpenTelemetry OTLP/HTTP** (JSON, gzip) to
  `POST /v1/logs` with `X-API-Key`: resource `host.name`, `service.name`, `os.type`,
  `host.arch`; per record the body, `observedTimeUnixNano`, `log.file.path`/`log.file.name`.
  logpipe's own extras use the `logpipe.*` namespace (source, tags, the host's UTC offset).
  Failed batches go to `run/buffer/` and are retried with backoff up to 1 min; batches an older
  agent buffered in logpipe's previous format are still sent, to the deprecated endpoint.
- **Heartbeat** every 15 s with version, sources, per-source counters and the hash of
  `remote.yaml`. When the server holds a different remote config, the agent fetches it, validates
  it, writes `conf.d/remote.yaml` and reloads. Hand-written files are never touched.
- **journald** uses `journalctl`. In the container it reads the host journal through the `/var/log`
  mount and also needs `-v /etc/machine-id:/etc/machine-id:ro` (journalctl only shows this
  machine's journal). On hosts with a volatile journal, add `-v /run/log/journal:/run/log/journal:ro`.
- **docker** reads the Engine API on `/var/run/docker.sock`, one stream per container.
