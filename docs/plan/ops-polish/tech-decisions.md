# Ops polish: technical decisions

| Decision | Choice | Rejected |
|---|---|---|
| Default password | `Auth.ensure()` stores `changeme` with `default: true` in the auth doc. `LOGPIPE_ADMIN_PASSWORD` still wins, and stored passwords are kept. | A settings knob for the default |
| Enforcement | The `require_login` middleware answers 403 on guarded routes except `/api/auth/*` while the flag is set. `/api/auth/status` reports `must_change`. The new password can't be the default. | A UI-only check, which the API would bypass |
| UI | `LoginGate` shows a *Set a new password* screen for a `must_change` session. | |
| Showcase | Reproduced under a pty first; the fix follows the root cause (see the PR). | |
| Agent build | `.github/workflows/agent-release.yml` with a plain `go build` matrix, the same flags as the image, `checksums.txt`, uploaded with `gh release upload`. A manual run only keeps workflow artifacts. | GoReleaser (a config file and a dependency for 5 builds) |
| Installer | POSIX `agent/install.sh`, a release asset. Detects OS/arch, verifies sha256, installs to `~/.local/bin` (`/usr/local/bin` as root). `LOGPIPE_AGENT_VERSION` and `LOGPIPE_AGENT_BASE_URL` overrides. | Package repositories |
