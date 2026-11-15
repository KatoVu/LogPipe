#!/bin/sh
# Install logpipe-agent from a GitHub release:
#   curl -fsSL https://github.com/KatoVu/LogPipe/releases/latest/download/install.sh | sh
# Env: LOGPIPE_AGENT_VERSION (e.g. v1.0.1; default: latest), LOGPIPE_AGENT_INSTALL_DIR
# (default: ~/.local/bin, /usr/local/bin as root), LOGPIPE_AGENT_BASE_URL (a mirror).
set -eu

repo=KatoVu/LogPipe
version=${LOGPIPE_AGENT_VERSION:-latest}
if [ -n "${LOGPIPE_AGENT_BASE_URL:-}" ]; then
	base=$LOGPIPE_AGENT_BASE_URL
elif [ "$version" = latest ]; then
	base=https://github.com/$repo/releases/latest/download
else
	base=https://github.com/$repo/releases/download/$version
fi

fail() { echo "logpipe-agent install: $*" >&2; exit 1; }

case $(uname -s) in
	Linux) os=linux ;;
	Darwin) os=darwin ;;
	*) fail "unsupported OS $(uname -s): use the docker image instead" ;;
esac
case $(uname -m) in
	x86_64 | amd64) arch=amd64 ;;
	aarch64 | arm64) arch=arm64 ;;
	armv7l | armv7*) arch=armv7 ;;
	*) fail "unsupported CPU $(uname -m)" ;;
esac
[ "$os/$arch" = darwin/armv7 ] && fail "unsupported platform darwin/armv7"

if [ -n "${LOGPIPE_AGENT_INSTALL_DIR:-}" ]; then
	dir=$LOGPIPE_AGENT_INSTALL_DIR
elif [ "$(id -u)" = 0 ]; then
	dir=/usr/local/bin
else
	dir=$HOME/.local/bin
fi

if command -v curl >/dev/null 2>&1; then
	fetch() { curl -fsSL -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
	fetch() { wget -qO "$2" "$1"; }
else
	fail "needs curl or wget"
fi
if command -v sha256sum >/dev/null 2>&1; then
	sha() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
	sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
	fail "needs sha256sum or shasum to verify the download"
fi

tarball=logpipe-agent_${os}_${arch}.tar.gz
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "downloading $tarball ($version)"
fetch "$base/$tarball" "$tmp/$tarball" || fail "download failed: $base/$tarball"
fetch "$base/checksums.txt" "$tmp/checksums.txt" || fail "download failed: $base/checksums.txt"
want=$(awk -v f="$tarball" '$2 == f { print $1 }' "$tmp/checksums.txt")
[ -n "$want" ] || fail "$tarball is not in checksums.txt"
[ "$(sha "$tmp/$tarball")" = "$want" ] || fail "checksum mismatch for $tarball"

tar -xzf "$tmp/$tarball" -C "$tmp" logpipe-agent
mkdir -p "$dir"
mv "$tmp/logpipe-agent" "$dir/logpipe-agent"
chmod 755 "$dir/logpipe-agent"
echo "installed $("$dir/logpipe-agent" version 2>/dev/null || echo logpipe-agent) to $dir/logpipe-agent"
case ":$PATH:" in
	*":$dir:"*) ;;
	*) echo "note: $dir is not on your PATH; add it, or run $dir/logpipe-agent" ;;
esac
cat <<EOF
next:
  logpipe-agent init --endpoint http://YOUR-SERVER:8080 --api-key lp_...
  logpipe-agent track '/var/log/myapp/*.log' --service myapp
  logpipe-agent check && logpipe-agent run
EOF
