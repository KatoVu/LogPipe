# Install the agent

`logpipe-agent` is a small Go collector (one static binary) that tails logs on a host and ships
them to `logpipe server` as OpenTelemetry OTLP/HTTP. It reads files (with rotation, and dated or
numbered log series),
journald, Docker containers and syslog (TCP/UDP), joins multiline events such as stack
traces, and can drop or mask lines before they leave the host.

**Before you start**, in the logpipe UI open **Settings → API keys** and create a key with use
**Agent: ships logs** (scope `intake`). You need the key (`lp_…`) and the server URL, for example
`http://logpipe.example:8080`.

=== "Container"

    Put in your server URL and key, then run:

    ```sh
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
    
    | Part | What it is for |
    |---|---|
    | `-d --restart unless-stopped` | runs in the background and comes back after a crash or reboot |
    | `LOGPIPE_ENDPOINT`, `LOGPIPE_API_KEY` | your server and the key you just created |
    | `LOGPIPE_HOSTNAME=$(hostname)` | shows the host's name on the **Agents** page instead of the container ID |
    | `-v /var/log:/var/log:ro` | the log files it can tail (read-only) |
    | `-v /var/run/docker.sock:…:ro` | lets it read other containers' logs |
    | `-v logpipe-agent:/var/lib/logpipe-agent` | keeps file offsets and the send buffer across restarts |
    | `-v "$PWD/conf.d:/etc/logpipe-agent/conf.d"` | keeps the sources you add with `track`; leave it out if you only add sources in the UI |
    
    For journald sources add `-v /etc/machine-id:/etc/machine-id:ro` (journalctl only shows
      this machine's journal, found through the `/var/log` mount); on hosts with a volatile
      journal also add `-v /run/log/journal:/run/log/journal:ro`.
      
    The image is built for amd64, arm64 and arm/v7. It has no `journalctl`, so for journald
    sources use the binary. To build it yourself: `docker build -t logpipe-agent agent/`. Next:
    [use the container](#use-the-container).

=== "Binary"

    Each release has binaries for Linux (amd64, arm64, armv7) and macOS (amd64, arm64). The
    installer picks yours, checks its sha256 and installs to `~/.local/bin` (`/usr/local/bin` as
    root):

    ```sh
    curl -fsSL https://github.com/KatoVu/LogPipe/releases/latest/download/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"   # where it goes without root; add this line to your shell profile
    ```

    `LOGPIPE_AGENT_VERSION=v1.0.1` pins a release, `LOGPIPE_AGENT_INSTALL_DIR` changes the target
    directory, and `LOGPIPE_AGENT_BASE_URL` points at a mirror. Next:
    [use the binary](#use-the-binary).

=== "From source (Go)"

    Needs Go 1.23+.

    ```sh
    git clone https://github.com/KatoVu/LogPipe.git
    cd LogPipe/agent
    go build -o logpipe-agent .
    sudo install -m 0755 logpipe-agent /usr/local/bin/   # or keep it anywhere on your PATH
    ```

    Cross-compile for a gateway: `GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o logpipe-agent .`

    No root is needed. As a normal user the config lives in `~/.config/logpipe-agent` (offsets
    and buffer in its `run/`); as root in `/etc/logpipe-agent`. To read most of `/var/log`
    without root, add yourself to the `adm` group once: `sudo usermod -aG adm $USER`, then log out
    and back in.

## Use the binary

**Set it up**: point it at the server and tell it what to collect.

```sh
logpipe-agent init --endpoint http://logpipe.example:8080 --api-key lp_xxx
logpipe-agent track '/var/log/myapp/*.log' --service myapp --source java --auto-multiline
logpipe-agent track journald:nginx.service --service nginx --source nginx
logpipe-agent check        # validates config, counts matching files, tests the server and key
```

**Start it** in the background. It starts at boot and restarts after a crash
([details](#run-in-the-background-binary)):

```sh
logpipe-agent service install
```

To try it in the foreground first, use `logpipe-agent run` (Ctrl+C stops it). The host appears
on the **Agents** page within a second of connecting.

## Use the container

The container is already running. Add sources and check on it through `docker exec`; a short
alias saves typing:

```sh
alias lpa='docker exec logpipe-agent logpipe-agent'
lpa track '/var/log/myapp/*.log' --service myapp --source java   # paths as seen inside: /var/log
lpa status                                                       # files, line counts, send errors
docker logs -f logpipe-agent                                     # the agent's own log
```

Sources can also be added in the UI (**Agents → your host → Push sources**). `track` writes into
the mounted `conf.d` and the agent reloads by itself, so there is nothing to restart.

**Upgrade** (offsets and the buffer stay in the `logpipe-agent` volume):

```sh
docker pull ghcr.io/katovu/logpipe-agent:latest
docker rm -f logpipe-agent
# then the same docker run as above
```

**Remove**: `docker rm -f logpipe-agent && docker volume rm logpipe-agent`.

## Basic commands

| Command | What it does |
|---|---|
| `init --endpoint URL [--api-key KEY] [--hostname H]` | write `agent.yaml` |
| `track TARGET [flags]` | add a source (`conf.d/<service>.yaml`) |
| `untrack TARGET` | remove a source you added (UI-pushed ones are removed in the UI) |
| `list` | every source and its rules |
| `check` | validate the config, the files and the connection |
| `run` | run in the foreground |
| `service install` / `service uninstall` | run it in the background at boot (systemd or launchd) |
| `status` | what the running agent is doing: files, line counts, send errors, buffer size |
| `config show` | effective config, API key redacted |
| `config get KEY` / `config set KEY VALUE` | read or change `agent.yaml`, keeping comments |
| `config edit [FILE]` | edit `agent.yaml` or `conf.d/FILE.yaml` in `$EDITOR`, saved only if valid |
| `version` | the agent's version |

`TARGET` is a file glob or [name format](#dated-and-numbered-log-files), `journald` or
`journald:UNIT`, `docker`, `tcp:PORT` or `udp:PORT`.
Useful `track` flags: `--service`, `--source`, `--tag k:v`, `--auto-multiline`,
`--multiline REGEX`, `--exclude REGEX`, `--include REGEX`, `--mask 'REGEX=>REPLACEMENT'`,
`--label key=value` (docker), `--start tail|end|beginning`, `--tail-lines N`.

`track` starts a source with **`--start tail`**: the last 1000 lines (`--tail-lines`) of a file,
container or journald unit already there are shipped once, then it's followed live. A new source
then has enough history for the server to draft its templates right away, instead of waiting for
fresh traffic. In `conf.d` this is `start_position: tail` (with `tail_lines`); without the key a
source starts at the end, like `tail -f`.

The container takes the same commands through `docker exec` ([see above](#use-the-container)).

## Run in the background (binary)

`logpipe-agent run` stays in the foreground. `service install` hands it to the OS instead, so it
starts at boot and restarts after a crash. It uses the binary's own path and the config directory
from `init`: a systemd user unit on Linux, a launchd agent on macOS, a system-wide one as root.

The service starts without your shell's environment, so `agent.yaml` must hold the key and
endpoint itself (`init --api-key lp_…` does that). If it still reads `${LOGPIPE_API_KEY}`,
`service install` refuses; set it with `logpipe-agent config set api_key lp_…`.

**Install the service:**

```sh
logpipe-agent service install
loginctl enable-linger $USER      # Linux as a normal user: keep it running after you log out
```

**Use it:**

```sh
logpipe-agent status                     # what it is doing
journalctl --user -u logpipe-agent -f    # its log (as root: journalctl -u logpipe-agent -f; macOS: agent.log in the config directory)
logpipe-agent service uninstall          # stop it and remove the service
```

**By hand, as a dedicated `logpipe` user** (system-wide, not root). Install as root so the binary is in
`/usr/local/bin`, where the unit expects it:

```sh
curl -fsSL https://github.com/KatoVu/LogPipe/releases/latest/download/install.sh | sudo sh
```

```ini title="/etc/systemd/system/logpipe-agent.service"
[Unit]
Description=logpipe agent
After=network-online.target

[Service]
User=logpipe
Group=adm
Environment=LOGPIPE_AGENT_CONFIG=/etc/logpipe-agent
ExecStart=/usr/local/bin/logpipe-agent run
Restart=always

[Install]
WantedBy=multi-user.target
```

```sh
sudo useradd --system --no-create-home logpipe
sudo mkdir -p /etc/logpipe-agent && sudo chown logpipe /etc/logpipe-agent
sudo -u logpipe LOGPIPE_AGENT_CONFIG=/etc/logpipe-agent logpipe-agent init --endpoint http://logpipe.example:8080 --api-key lp_xxx
sudo systemctl enable --now logpipe-agent
```

## Configuration in brief

```yaml title="agent.yaml"
api_key: ${LOGPIPE_API_KEY}       # ${VAR} is read from the environment
endpoint: https://logpipe.example:8080
hostname: gw-01                   # default: the system hostname
tags: [env:prod, site:hanoi]      # added to every event
buffer_mb: 100                    # disk buffer while the server is down
remote_config: true               # let the UI push sources (conf.d/remote.yaml)
```

```yaml title="conf.d/myapp.yaml"
logs:
  - type: file
    path: /var/log/myapp/*.log
    service: myapp
    source: java
    log_processing_rules:
      - type: multi_line
        name: new_event
        pattern: '^\d{4}-\d{2}-\d{2}'
      - type: mask_sequences
        pattern: 'password=\S+'
        replace_placeholder: 'password=[REDACTED]'
```

## Dated and numbered log files

Many apps write a new file per day or per part: `20-11-2026.txt`, `21-11-2026.txt`, or `01.txt`,
`02.txt`. Put placeholders where the name changes and the agent tails **only the latest file**:

```yaml title="conf.d/myapp.yaml"
logs:
  - type: file
    path: /var/log/myapp/{DD}-{MM}-{YYYY}.txt   # newest date is tailed
    service: myapp
  - type: file
    path: /var/log/worker/{N}.txt               # highest number is tailed
    latest: lowest                              # ...or the lowest, if that is the newest
```

| Placeholder | Matches | Placeholder | Matches |
|---|---|---|---|
| `{YYYY}` | 4-digit year | `{HH}` | 2-digit hour |
| `{MM}` | 2-digit month | `{N}` | any number, compared as a number (`2` < `10`) |
| `{DD}` | 2-digit day | `*` `?` | still work in the rest of the path |

Files are ordered by date first, then by `{N}`. How it behaves:

- When a later file appears, the previous one is still read for **one minute** to catch late
  writes, then closed.
- Older files are never replayed, even with `start_position: beginning`. They are listed in
  `logpipe-agent status` as *not tailed* and counted on the agent page.
- A file that started while the agent was stopped is read from its start, and anything the last
  run left unread is finished on start.
- Without `service:`, the service is the fixed start of the name (`app-{YYYY}-{MM}-{DD}.log` →
  `app`), else the folder (`myapp/{N}.txt` → `myapp`).

**Let `track` write it for you**: `logpipe-agent track '/var/log/myapp/*.txt'` looks at the files
that match. If they share one name format, it saves the path with placeholders and tells you. Check
it with `logpipe-agent list` and fix it with `logpipe-agent config edit myapp` if it guessed
wrong. Dates like `11-20-2026` (month first) are read as day first, so change those to
`{MM}-{DD}-{YYYY}`. Sources pushed from the UI are not detected; write the placeholders yourself
(the **Dated / numbered files** snippet is a starting point).

Files are followed by identity (device, inode and their first 1 KB), not by name, so a file
renamed inside the glob, such as `app.log` → `app.log.1`, is never read twice.

The full reference (every rule type, Docker labels, syslog listeners, how rotation and the
buffer behave) is in
[agent/README.md](https://github.com/KatoVu/LogPipe/blob/main/agent/README.md).

!!! tip "No agent? Use OpenTelemetry"
    Anything that sends OTLP/HTTP logs works too: point an OpenTelemetry Collector's `otlphttp`
    exporter at `http://<server>:8080/v1/logs` with the header `X-API-Key: lp_xxx`
    ([details](../agent-platform.md#intake-api-agents)).
