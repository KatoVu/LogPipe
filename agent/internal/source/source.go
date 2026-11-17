// Package source reads raw lines from files, journald, docker containers and syslog listeners.
package source

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jakekato/logpipe/agent/internal/config"
)

// Line is one raw line. Key separates independent streams inside a source (one file, one
// container, one syslog peer) so multiline aggregation never mixes them.
type Line struct {
	Key     string
	Text    string
	Time    time.Time
	Service string // overrides the source's service when the source knows better (container, unit)
	Tags    []string
}

// Stats is what `status` and the heartbeat report per source.
type Stats struct {
	ID       string   `json:"id"`
	Type     string   `json:"type"`
	Target   string   `json:"target"`
	Service  string   `json:"service"`
	Lines    int64    `json:"lines"`
	Bytes    int64    `json:"bytes"`
	LastLine string   `json:"last_line_at,omitempty"`
	Files    []string `json:"files,omitempty"`
	// Skipped are matched files released because a newer file of their series took over.
	Skipped []string `json:"skipped_files,omitempty"`
	Error   string   `json:"error,omitempty"`
}

type Tracker struct {
	mu      sync.Mutex
	lines   atomic.Int64
	bytes   atomic.Int64
	last    atomic.Int64
	files   []string
	skipped []string
	err     string
}

func (t *Tracker) Count(text string) {
	t.lines.Add(1)
	t.bytes.Add(int64(len(text)))
	t.last.Store(time.Now().UnixMilli())
}

func (t *Tracker) SetError(err error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.err = ""
	if err != nil {
		t.err = err.Error()
	}
}

func (t *Tracker) SetFiles(files []string) {
	t.mu.Lock()
	t.files = files
	t.mu.Unlock()
}

func (t *Tracker) SetSkipped(files []string) {
	t.mu.Lock()
	t.skipped = files
	t.mu.Unlock()
}

func (t *Tracker) Snapshot(src config.Source) Stats {
	t.mu.Lock()
	defer t.mu.Unlock()
	s := Stats{ID: src.ID(), Type: src.Type, Target: Target(src), Service: src.Service,
		Lines: t.lines.Load(), Bytes: t.bytes.Load(), Files: t.files, Skipped: t.skipped, Error: t.err}
	if ms := t.last.Load(); ms > 0 {
		s.LastLine = time.UnixMilli(ms).UTC().Format(time.RFC3339)
	}
	return s
}

func Target(s config.Source) string {
	switch s.Type {
	case "file":
		return s.Path
	case "journald":
		if s.Unit != "" {
			return s.Unit
		}
		return "all units"
	case "docker":
		return "containers"
	default:
		return s.Type + ":" + strconv.Itoa(s.Port)
	}
}

// Reader runs one source until ctx is done, sending lines to out.
type Reader func(ctx context.Context, src config.Source, pos *Positions, tr *Tracker, out chan<- Line) error

func For(src config.Source) Reader {
	switch src.Type {
	case "file":
		return TailFiles
	case "journald":
		return Journald
	case "docker":
		return Docker
	default:
		return Syslog
	}
}

// Positions persists where each file/journal/container was read up to, so a restart neither
// resends nor skips lines.
type Positions struct {
	mu   sync.Mutex
	path string
	m    map[string]Position
}

type Position struct {
	Dev     uint64 `json:"dev,omitempty"`
	Inode   uint64 `json:"inode,omitempty"`
	Offset  int64  `json:"offset,omitempty"`
	Head    uint64 `json:"head,omitempty"`
	HeadLen int    `json:"head_len,omitempty"`
	Cursor  string `json:"cursor,omitempty"`
}

func LoadPositions(path string) *Positions {
	p := &Positions{path: path, m: map[string]Position{}}
	if b, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(b, &p.m)
	}
	return p
}

func (p *Positions) Get(key string) (Position, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	v, ok := p.m[key]
	return v, ok
}

func (p *Positions) Set(key string, v Position) {
	p.mu.Lock()
	p.m[key] = v
	p.mu.Unlock()
}

// Find returns the saved position of an open file under any name, for a file renamed while the
// agent was stopped.
func (p *Positions) Find(t *tailed) (Position, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, v := range p.m {
		if v.Inode == t.id.Ino && t.same(v) {
			return v, true
		}
	}
	return Position{}, false
}

// Prune forgets files that match glob but no longer exist, so dated logs that were deleted do
// not pile up in positions.json. Keys that are not paths never match a path glob.
func (p *Positions) Prune(glob string, matches []string) {
	exists := make(map[string]bool, len(matches))
	for _, m := range matches {
		exists[m] = true
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	for k := range p.m {
		if ok, _ := filepath.Match(glob, k); ok && !exists[k] {
			delete(p.m, k)
		}
	}
}

func (p *Positions) Save() error {
	p.mu.Lock()
	b, _ := json.MarshalIndent(p.m, "", "  ")
	p.mu.Unlock()
	if err := os.MkdirAll(filepath.Dir(p.path), 0o755); err != nil {
		return err
	}
	tmp := p.path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p.path)
}

func send(ctx context.Context, out chan<- Line, l Line) bool {
	select {
	case out <- l:
		return true
	case <-ctx.Done():
		return false
	}
}
