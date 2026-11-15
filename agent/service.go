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
	"strings"

	"gopkg.in/yaml.v3"
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
	// systemd reads user units from $XDG_CONFIG_HOME/systemd/user, which UserConfigDir honours.
	base, err := os.UserConfigDir()
	if runtime.GOOS == "darwin" {
		base, err = os.UserHomeDir()
		base = filepath.Join(base, "Library")
	}
	if err != nil {
		return err
	}
	root := os.Geteuid() == 0
	path, body, err := serviceFile(runtime.GOOS, root, exe, dir, base)
	if err != nil {
		return err
	}
	ctl := serviceCtl(runtime.GOOS, root, path, dir)

	if args[0] == "uninstall" {
		if _, err := os.Stat(path); os.IsNotExist(err) {
			fmt.Fprintln(out, "not installed:", path, "doesn't exist")
			return nil
		}
		if err := ctl.stop(); err != nil { // keep the file, or a still-running agent is orphaned
			return fmt.Errorf("could not stop the service, left %s in place: %w", path, err)
		}
		if err := os.Remove(path); err != nil {
			return err
		}
		ctl.reload()
		fmt.Fprintln(out, "removed", path)
		return nil
	}
	raw, err := os.ReadFile(filepath.Join(dir, "agent.yaml"))
	if err != nil {
		return fmt.Errorf("no agent.yaml in %s: run `logpipe-agent init` first", dir)
	}
	if err := serviceReady(raw); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		return err
	}
	_ = ctl.stop() // reinstall: pick up the new file; on a fresh install there's nothing to stop
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
// base is the user's config dir on Linux and ~/Library on macOS.
func serviceFile(goos string, root bool, exe, dir, base string) (string, string, error) {
	switch goos {
	case "linux":
		path, target := filepath.Join(base, "systemd/user/logpipe-agent.service"), "default.target"
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
`, unitEscape(exe), unitEscape(dir), target), nil
	case "darwin":
		path := filepath.Join(base, "LaunchAgents/io.logpipe.agent.plist")
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

// unitEscape keeps a path literal in ExecStart: systemd expands %x and $VAR even inside quotes.
func unitEscape(s string) string { return strings.NewReplacer("%", "%%", "$", "$$").Replace(s) }

type ctl struct {
	stop   func() error
	reload func()
	start  func() error
	logs   string
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
			stop:   func() error { return sh("launchctl", "unload", "-w", path) },
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
		stop:   func() error { return systemctl("disable", "--now", "logpipe-agent") },
		reload: func() { systemctl("daemon-reload") },
		start:  func() error { return systemctl("enable", "--now", "logpipe-agent") },
		logs:   logs,
	}
}

// serviceReady refuses an agent.yaml whose key or endpoint comes from the environment: systemd and
// launchd start the service without your shell's variables, so it would run with no key.
func serviceReady(raw []byte) error {
	var c struct {
		APIKey   string `yaml:"api_key"`
		Endpoint string `yaml:"endpoint"`
	}
	if err := yaml.Unmarshal(raw, &c); err != nil {
		return err
	}
	for k, v := range map[string]string{"api_key": c.APIKey, "endpoint": c.Endpoint} {
		if v == "" || strings.Contains(v, "${") {
			return fmt.Errorf("agent.yaml %s is %q, but the service won't see your shell's environment: "+
				"set it first with `logpipe-agent config set %s VALUE`", k, v, k)
		}
	}
	return nil
}
