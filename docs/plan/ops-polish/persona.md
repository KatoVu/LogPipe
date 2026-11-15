# Ops polish: persona

**Who.** Someone who runs `logpipe server` on a VM or demos it with `logpipe showcase`. Comfortable
with Linux, not a Go developer.

**Problems, and what they cost.**

1. A new server's random password is printed once in the console and is easy to lose. The only
   way back in is to set an env var and restart.
2. The showcase's template-drafting act gets stuck, so the demo breaks halfway, in front of an
   audience.
3. Installing the agent without Docker means installing Go and building it.

**Solutions.** A known default password that must be replaced at the first login, a fixed showcase
act, and agent binaries on every GitHub Release with a one-line installer.

**Out of scope.** Users and roles, email password reset, installing a systemd service (the README
has a sample unit), Windows builds, .deb/.rpm packages, the server hosting agent binaries.
