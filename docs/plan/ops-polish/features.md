# Ops polish: features

| # | Feature | Solves |
|---|---|---|
| 1 | **Default password + forced change.** A new server starts with `changeme`. Logging in with it shows only *Set a new password*, and every other `/api` route answers 403 until it changes. | Lost first password (1) |
| 2 | **Showcase draft act fixed.** Doing the act in the UI, or pressing Enter after waiting, both move on to the review. | Broken demo (2) |
| 3 | **Agent release binaries + `install.sh`.** Linux amd64/arm64/armv7 and macOS amd64/arm64 tarballs with checksums on every `v*` release. `curl … \| sh` installs to `~/.local/bin`. | Agent needs Go (3) |
