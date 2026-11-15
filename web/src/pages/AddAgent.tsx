// Adding an agent: get a key, run one command on the host, watch it connect. Used as a side
// panel on the Agents page and as steps of the setup guide.
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, useAction, usePoll, type Agent } from "../api";
import { CopyBlock, Drawer, Stepper } from "../ui";

export function installCommands(token: string) {
  const endpoint = window.location.origin;
  return {
    docker: [
      "docker run -d --name logpipe-agent --restart unless-stopped \\",
      `  -e LOGPIPE_ENDPOINT=${endpoint} \\`,
      `  -e LOGPIPE_API_KEY=${token} \\`,
      "  -e LOGPIPE_HOSTNAME=$(hostname) \\",
      "  -v /var/log:/var/log:ro \\",
      "  -v /var/run/docker.sock:/var/run/docker.sock:ro \\",
      "  -v logpipe-agent:/var/lib/logpipe-agent \\",
      "  ghcr.io/katovu/logpipe-agent:latest",
    ].join("\n"),
    binary: [
      "curl -fsSL https://github.com/KatoVu/LogPipe/releases/latest/download/install.sh | sh",
      'export PATH="$HOME/.local/bin:$PATH"',
      `logpipe-agent init --endpoint ${endpoint} --api-key ${token}`,
      "logpipe-agent track '/var/log/myapp/*.log' --service myapp --auto-multiline",
      "logpipe-agent check && logpipe-agent service install",
    ].join("\n"),
  };
}

/** Create a key (shown once) or paste one you already have. */
export function KeyStep({ token, onToken }: { token: string; onToken: (t: string) => void }) {
  const [mode, setMode] = useState<"new" | "have">("new");
  const [name, setName] = useState("agents");
  const [pasted, setPasted] = useState("");
  const create = useAction((n: string) => api<{ token: string }>("/api/keys", { method: "POST", json: { name: n } }));
  if (token) {
    return (
      <div className="stack">
        <div className="callout good"><div className="grow"><strong>Key ready.</strong> Copy it now: a new key is only shown once.</div></div>
        <CopyBlock text={token} />
        <button type="button" className="btn small ghost" style={{ alignSelf: "flex-start" }} onClick={() => onToken("")}>Use a different key</button>
      </div>
    );
  }
  return (
    <div className="stack">
      <div className="seg" role="group" aria-label="Key">
        <button type="button" className={mode === "new" ? "on" : ""} aria-pressed={mode === "new"} onClick={() => setMode("new")}>Create a new key</button>
        <button type="button" className={mode === "have" ? "on" : ""} aria-pressed={mode === "have"} onClick={() => setMode("have")}>I have a key</button>
      </div>
      {mode === "new" ? (
        <form className="row" onSubmit={(e) => { e.preventDefault(); create.mutate(name, { onSuccess: (r) => onToken(r.token) }); }}>
          <label className="field">Name <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. production" /></label>
          <button className="btn primary" style={{ alignSelf: "flex-end" }} disabled={create.isPending}>Create key</button>
        </form>
      ) : (
        <form className="row" onSubmit={(e) => { e.preventDefault(); onToken(pasted.trim()); }}>
          <label className="field grow">API key <input className="mono" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="lp_…" /></label>
          <button className="btn primary" style={{ alignSelf: "flex-end" }} disabled={!pasted.trim().startsWith("lp_")}>Use it</button>
        </form>
      )}
      <p className="muted small" style={{ margin: 0 }}>Agents send the key with every request. Revoke it any time under <Link to="/settings#keys">Settings → API keys</Link>.</p>
      {create.error && <div className="error-text">{create.error.message}</div>}
    </div>
  );
}

export function InstallStep({ token }: { token: string }) {
  const [how, setHow] = useState<"docker" | "binary">("docker");
  const cmds = installCommands(token || "<your API key>");
  return (
    <div className="stack">
      <div className="seg" role="group" aria-label="Install with">
        <button type="button" className={how === "docker" ? "on" : ""} aria-pressed={how === "docker"} onClick={() => setHow("docker")}>Docker</button>
        <button type="button" className={how === "binary" ? "on" : ""} aria-pressed={how === "binary"} onClick={() => setHow("binary")}>Binary</button>
      </div>
      <p className="muted small" style={{ margin: 0 }}>Run this on the host whose logs you want.
        {how === "docker" ? " It reads /var/log and container output. For journald, add -v /etc/machine-id:/etc/machine-id:ro." : " Installs the binary for Linux or macOS to ~/.local/bin; no sudo needed. Your user must be able to read the files (e.g. be in the adm group)."}</p>
      <CopyBlock text={cmds[how]} />
    </div>
  );
}

/** How to get a host connected, and what to check when it doesn't show up. */
export function ConnectHelp({ token }: { token: string }) {
  const origin = window.location.origin;
  const local = /^(localhost|127\.|\[::1\]$)/.test(window.location.hostname);
  return (
    <div className="stack small">
      <ol style={{ margin: 0, paddingLeft: 18 }}>
        <li>Run the install command on the host. Docker starts it in the background; for the binary, <code>service install</code> does.</li>
        <li>The agent connects to <code>{origin}</code> with your key and shows up here within a second.</li>
        <li>Nothing yet? Test the address and key from the host. <code>{"{\"ok\": true, …}"}</code> means both work:</li>
      </ol>
      <CopyBlock text={`curl -fsS -H "X-API-Key: ${token || "<your API key>"}" ${origin}/intake/v1/ping`} />
      {local && (
        <div className="callout warn"><div className="grow">
          You opened this page on <code>{window.location.hostname}</code>, so the commands point at <code>{origin}</code>. Other hosts and containers can't reach that:
          use this server's IP or name instead (e.g. <code>http://192.168.1.10:8080</code>). For Docker on this same machine, add <code>--network host</code>: that works as is with Docker Engine on Linux; Docker Desktop (macOS, Windows) needs version 4.34+ with host networking turned on under Settings → Resources → Network.
        </div></div>
      )}
      <details>
        <summary>Still not connecting?</summary>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          <li><strong>401</strong>: the key is wrong or revoked. Create a new one in the first step.</li>
          <li><strong>Connection refused or timeout</strong>: the URL is wrong, or a firewall blocks the port.</li>
          <li>Binary: <code>logpipe-agent check</code> tests the config, the files and the server; <code>logpipe-agent status</code> shows send errors.</li>
          <li>Docker: <code>docker logs logpipe-agent</code>.</li>
        </ul>
      </details>
    </div>
  );
}

/** Watches for a host that wasn't connected when this opened. */
export function WaitStep({ known, token = "", onOpen }: { known: Set<string>; token?: string; onOpen?: () => void }) {
  const { data } = usePoll<{ agents: Agent[] }>("/api/agents", 2000);
  const fresh = data?.agents.filter((a) => !known.has(a.hostname) && a.online) ?? [];
  if (!fresh.length) {
    return (
      <div className="stack">
        <div className="waiting" role="status"><span className="spin" aria-hidden="true" /><div><strong>Waiting for the agent to connect…</strong></div></div>
        <ConnectHelp token={token} />
      </div>
    );
  }
  return (
    <div className="stack">
      {fresh.map((a) => (
        <div key={a.hostname} className="callout good">
          <div className="grow"><strong>{a.hostname}</strong> connected · {a.status.sources?.length ?? 0} sources · {a.status.os}/{a.status.arch}</div>
          <Link className="btn small primary" to={`/agents/${encodeURIComponent(a.hostname)}`} onClick={onOpen}>Open agent</Link>
        </div>
      ))}
      <p className="muted small" style={{ margin: 0 }}>Open the agent to choose which files it collects, or track them on the host with <code>logpipe-agent track</code>.</p>
    </div>
  );
}

export function AddAgentDrawer({ onClose }: { onClose: () => void }) {
  const agents = usePoll<{ agents: Agent[] }>("/api/agents", 3000).data?.agents;
  const known = useRef<Set<string> | null>(null);
  if (agents && !known.current) known.current = new Set(agents.filter((a) => a.online).map((a) => a.hostname));
  const [step, setStep] = useState(0);
  const [token, setToken] = useState("");
  const steps = [{ label: "API key", done: !!token }, { label: "Install", done: step > 1 }, { label: "Connect", done: false }];
  return (
    <Drawer onClose={onClose}>
      <div className="stack">
        <h2>Add an agent</h2>
        <Stepper steps={steps} current={step} onPick={setStep} />
        <div className="wizard-body">
          {step === 0 && <KeyStep token={token} onToken={setToken} />}
          {step === 1 && <InstallStep token={token} />}
          {step === 2 && <WaitStep known={known.current ?? new Set()} token={token} onOpen={onClose} />}
        </div>
        <div className="wizard-foot">
          {step > 0 ? <button className="btn" onClick={() => setStep(step - 1)}>Back</button> : <span />}
          {step < 2 ? (
            <button className="btn primary" disabled={step === 0 && !token} onClick={() => setStep(step + 1)}>
              {step === 1 ? "I ran it" : "Next"}
            </button>
          ) : <button className="btn" onClick={onClose}>Done</button>}
        </div>
      </div>
    </Drawer>
  );
}
