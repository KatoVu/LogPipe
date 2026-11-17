package config

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
)

// A file path may name the parts of a log series that change with placeholders:
//
//	/var/log/myapp/{DD}-{MM}-{YYYY}.txt   /var/log/myapp/{N}.txt   /var/log/app-{YYYY}{MM}{DD}-{N}.log
//
// Only the latest file of the series is tailed: the newest date, then the highest {N}
// (or the lowest, with latest: lowest).
var tokenRE = regexp.MustCompile(`\{([A-Za-z]*)\}`)

var tokenPattern = map[string]string{"YYYY": `\d{4}`, "MM": `\d{2}`, "DD": `\d{2}`, "HH": `\d{2}`, "N": `\d+`}

// Format is a compiled file name format.
type Format struct {
	Glob string // the path with every placeholder as *, for filepath.Glob
	re   *regexp.Regexp
}

// HasFormat reports whether a path uses placeholders.
func HasFormat(path string) bool { return tokenRE.MatchString(path) }

// Format compiles s.Path; it is nil for a path without placeholders.
func (s Source) Format() (*Format, error) {
	if !HasFormat(s.Path) {
		return nil, nil
	}
	seen := map[string]bool{}
	var re strings.Builder
	re.WriteString("^")
	last := 0
	for _, m := range tokenRE.FindAllStringSubmatchIndex(s.Path, -1) {
		re.WriteString(globToRegexp(s.Path[last:m[0]]))
		name := s.Path[m[2]:m[3]]
		if tokenPattern[name] == "" {
			return nil, fmt.Errorf("unknown placeholder {%s} in path (use {YYYY} {MM} {DD} {HH} {N})", name)
		}
		if seen[name] {
			return nil, fmt.Errorf("placeholder {%s} appears twice in path", name)
		}
		seen[name] = true
		fmt.Fprintf(&re, "(?P<%s>%s)", name, tokenPattern[name])
		last = m[1]
	}
	re.WriteString(globToRegexp(s.Path[last:]) + "$")
	return &Format{Glob: tokenRE.ReplaceAllString(s.Path, "*"), re: regexp.MustCompile(re.String())}, nil
}

// globToRegexp turns the literal parts of a path into a regexp, keeping * and ? as globs.
func globToRegexp(s string) string {
	q := regexp.QuoteMeta(s)
	return strings.NewReplacer(`\*`, `[^/]*`, `\?`, `[^/]`).Replace(q)
}

// Key orders the files of a series: a larger key is a later file. ok is false for a path the
// format does not match (another file the glob happens to catch).
func (f *Format) Key(path string) (key string, ok bool) {
	m := f.re.FindStringSubmatch(path)
	if m == nil {
		return "", false
	}
	part := func(name, zero string) string {
		if i := f.re.SubexpIndex(name); i > 0 {
			return m[i]
		}
		return zero
	}
	n := strings.TrimLeft(part("N", ""), "0")
	return part("YYYY", "0000") + part("MM", "00") + part("DD", "00") + part("HH", "00") +
		fmt.Sprintf("%20s", n), true
}

// StaticName is the path's base name with its placeholders and what follows them dropped, for a
// default service name: app-{YYYY}-{MM}-{DD}.log -> app-, myapp/{N}.txt -> "" (callers fall back).
func StaticName(path string) string {
	base := filepath.Base(path)
	if loc := tokenRE.FindStringIndex(base); loc != nil {
		base = base[:loc[0]]
	}
	return base
}
