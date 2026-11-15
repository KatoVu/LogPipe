package main

import (
	"errors"
	"fmt"
	"html"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// cmdService installs `run` as a systemd unit (Linux) or launchd agent (macOS) that starts at boot
// and restarts on failure: a user unit for a normal user, a system one as root.
func cmdService(dir string, args []string, out io.Writer) error {
	if len(args) != 1 || (args[0] != "install" && args[0] != "uninstall") {
		return errors.New("usage: logpipe-agent service install | uninstall")
	}
	dir, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	exe, err := os.Executable()
	if err == nil {
		exe, err = filepath.EvalSymlinks(exe)
	}
	if err != nil {
		return err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	root := os.Geteuid() == 0
	path, body, err := serviceFile(runtime.GOOS, root, exe, dir, home)
	if err != nil {
		return err
	}
	ctl := serviceCtl(runtime.GOOS, root, path, dir)

	if args[0] == "uninstall" {
		ctl.stop() // not running or not loaded is fine
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
			return err
		}
		ctl.reload()
		fmt.Fprintln(out, "removed", path)
		return nil
	}
	if _, err := os.Stat(filepath.Join(dir, "agent.yaml")); err != nil {
		return fmt.Errorf("no agent.yaml in %s: run `logpipe-agent init` first", dir)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		return err
	}
	ctl.stop() // reinstall: pick up the new file
	ctl.reload()
	if err := ctl.start(); err != nil {
		return fmt.Errorf("wrote %s but could not start it: %w", path, err)
	}
	fmt.Fprintln(out, "installed and started", path)
	fmt.Fprintln(out, "  logs:", ctl.logs)
	if runtime.GOOS == "linux" && !root {
		fmt.Fprintln(out, "  to keep it running after you log out (and start it at boot): loginctl enable-linger $USER")
	}
	return nil
}

// serviceFile is where the service definition goes and what it says.
func serviceFile(goos string, root bool, exe, dir, home string) (string, string, error) {
	switch goos {
	case "linux":
		path, target := filepath.Join(home, ".config/systemd/user/logpipe-agent.service"), "default.target"
		if root {
			path, target = "/etc/systemd/system/logpipe-agent.service", "multi-user.target"
		}
		return path, fmt.Sprintf(`[Unit]
Description=logpipe-agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=%q -c %q run
Restart=always
RestartSec=5

[Install]
WantedBy=%s
`, exe, dir, target), nil
	case "darwin":
		path := filepath.Join(home, "Library/LaunchAgents/io.logpipe.agent.plist")
		if root {
			path = "/Library/LaunchDaemons/io.logpipe.agent.plist"
		}
		x := html.EscapeString
		return path, fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>io.logpipe.agent</string>
  <key>ProgramArguments</key>
  <array><string>%s</string><string>-c</string><string>%s</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>%s</string>
</dict>
</plist>
`, x(exe), x(dir), x(filepath.Join(dir, "agent.log"))), nil
	}
	return "", "", fmt.Errorf("service is only supported on Linux (systemd) and macOS (launchd), not %s", goos)
}

type ctl struct {
	stop, reload func()
	start        func() error
	logs         string
}

func serviceCtl(goos string, root bool, path, dir string) ctl {
	sh := func(name string, args ...string) error {
		b, err := exec.Command(name, args...).CombinedOutput()
		if err != nil && len(b) > 0 {
			return fmt.Errorf("%w: %s", err, b)
		}
		return err
	}
	if goos == "darwin" {
		return ctl{
			stop:   func() { sh("launchctl", "unload", "-w", path) },
			reload: func() {},
			start:  func() error { return sh("launchctl", "load", "-w", path) },
			logs:   "tail -f " + filepath.Join(dir, "agent.log"),
		}
	}
	sc := []string{}
	logs := "journalctl -u logpipe-agent -f"
	if !root {
		sc, logs = []string{"--user"}, "journalctl --user -u logpipe-agent -f"
	}
	systemctl := func(a ...string) error { return sh("systemctl", append(sc, a...)...) }
	return ctl{
		stop:   func() { systemctl("disable", "--now", "logpipe-agent") },
		reload: func() { systemctl("daemon-reload") },
		start:  func() error { return systemctl("enable", "--now", "logpipe-agent") },
		logs:   logs,
	}
}
