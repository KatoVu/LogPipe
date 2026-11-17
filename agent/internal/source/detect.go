package source

import (
	"path/filepath"
	"regexp"
	"strings"
)

// part is a piece of a file name: text, a placeholder, or a run of digits not yet explained.
type part struct {
	s   string
	num bool
}

var runRE = regexp.MustCompile(`\d+|\D+`)

// nameParts splits a file name into runs and turns date-shaped ones into placeholders:
// 2026-11-17 -> {YYYY}-{MM}-{DD}, 17-11-2026 -> {DD}-{MM}-{YYYY}, 20261117 -> {YYYY}{MM}{DD}.
func nameParts(name string) []part {
	runs := runRE.FindAllString(name, -1)
	digits := func(i, n int) bool {
		return i < len(runs) && len(runs[i]) == n && runs[i][0] >= '0' && runs[i][0] <= '9'
	}
	sep := func(i int) bool { return i < len(runs) && len(runs[i]) == 1 && strings.Contains("-_.", runs[i]) }
	date3 := func(i, a, b, c int) bool {
		return digits(i, a) && sep(i+1) && digits(i+2, b) && sep(i+3) && runs[i+3] == runs[i+1] && digits(i+4, c)
	}
	var out []part
	for i := 0; i < len(runs); {
		switch {
		case date3(i, 4, 2, 2):
			s := runs[i+1]
			out, i = append(out, part{s: "{YYYY}" + s + "{MM}" + s + "{DD}"}), i+5
		case date3(i, 2, 2, 4): // day first; a US month-first name has to be fixed by hand
			s := runs[i+1]
			out, i = append(out, part{s: "{DD}" + s + "{MM}" + s + "{YYYY}"}), i+5
		case digits(i, 8) && (strings.HasPrefix(runs[i], "19") || strings.HasPrefix(runs[i], "20")):
			out, i = append(out, part{s: "{YYYY}{MM}{DD}"}), i+1
		default:
			out, i = append(out, part{s: runs[i], num: runs[i][0] >= '0' && runs[i][0] <= '9'}), i+1
		}
	}
	return out
}

// DetectFormat looks at the files a glob matches today and, when they are one series in one
// folder, returns the path with placeholders (/var/log/myapp/*.txt -> /var/log/myapp/{N}.txt).
// Dates are recognised by shape; one run of digits that differs between files becomes {N}.
// ok is false when the files do not share one name format.
func DetectFormat(glob string) (string, bool) {
	matches, _ := filepath.Glob(glob)
	if len(matches) == 0 {
		return "", false
	}
	dir := filepath.Dir(matches[0])
	var tmpl []part
	counter := -1
	for _, m := range matches {
		if filepath.Dir(m) != dir {
			return "", false
		}
		p := nameParts(filepath.Base(m))
		if tmpl == nil {
			tmpl = p
			continue
		}
		if len(p) != len(tmpl) {
			return "", false
		}
		for j := range p {
			if p[j].s == tmpl[j].s {
				continue
			}
			if !p[j].num || !tmpl[j].num || counter >= 0 && counter != j {
				return "", false
			}
			counter = j
		}
	}
	var b strings.Builder
	for j, p := range tmpl {
		if j == counter {
			b.WriteString("{N}")
		} else {
			b.WriteString(p.s)
		}
	}
	name := b.String()
	if !strings.Contains(name, "{") {
		return "", false
	}
	return filepath.Join(dir, name), true
}
