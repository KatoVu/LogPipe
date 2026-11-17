package source

import (
	"bufio"
	"context"
	"errors"
	"hash/fnv"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/jakekato/logpipe/agent/internal/config"
)

// maxLine splits a line with no newline in sight (a binary file) so memory stays bounded.
const maxLine = 1 << 20

// PollEvery is how often globs are re-expanded and files checked for growth or rotation.
var PollEvery = 250 * time.Millisecond

// shortcut: polling instead of inotify costs a stat per file per 250ms; fine for tens of files, switch to fsnotify for thousands.

type tailed struct {
	f       *os.File
	r       *bufio.Reader
	id      fileID
	offset  int64
	partial []byte
	head    uint64 // hash of the first headLen bytes, to tell a reused inode from the same file
	headLen int
}

// fileID is what a file is, independent of its name: renames keep it, a new file changes it.
type fileID struct{ Dev, Ino uint64 }

func idOf(fi os.FileInfo) fileID {
	if st, ok := fi.Sys().(*syscall.Stat_t); ok {
		return fileID{uint64(st.Dev), st.Ino}
	}
	return fileID{}
}

// headSize is how much of a file's start is hashed; logrotate can hand a new file the inode of
// one it just deleted, and the first bytes tell them apart.
const headSize = 1024

func headOf(f *os.File, n int) (uint64, int) {
	buf := make([]byte, n)
	k, _ := f.ReadAt(buf, 0)
	h := fnv.New64a()
	h.Write(buf[:k])
	return h.Sum64(), k
}

// same reports whether a saved position belongs to this open file.
func (t *tailed) same(p Position) bool {
	if p.Inode != t.id.Ino || p.Dev != 0 && p.Dev != t.id.Dev { // Dev 0: saved before it was recorded
		return false
	}
	if p.HeadLen == 0 {
		return true
	}
	h, n := headOf(t.f, p.HeadLen)
	return n == p.HeadLen && h == p.Head
}

func (t *tailed) position() Position {
	if t.headLen < headSize && t.offset > int64(t.headLen) {
		t.head, t.headLen = headOf(t.f, headSize)
	}
	return Position{Dev: t.id.Dev, Inode: t.id.Ino, Offset: t.offset, Head: t.head, HeadLen: t.headLen}
}

// superseded lists files that a newer file of the same series (same directory and SeriesName)
// has replaced and that have been quiet for longer than quiet. A file with no newer sibling is
// never superseded, however long it stays quiet.
func superseded(stats map[string]os.FileInfo, quiet time.Duration, now time.Time) map[string]bool {
	if quiet <= 0 {
		return nil
	}
	series := func(p string) string { return filepath.Join(filepath.Dir(p), SeriesName(p)) }
	newest := map[string]time.Time{}
	for p, fi := range stats {
		if k := series(p); fi.ModTime().After(newest[k]) {
			newest[k] = fi.ModTime()
		}
	}
	out := map[string]bool{}
	for p, fi := range stats {
		if fi.ModTime().Before(newest[series(p)]) && now.Sub(fi.ModTime()) > quiet {
			out[p] = true
		}
	}
	return out
}

// resume finds where to continue an opened file: its saved offset under this name, or under any
// name (renamed while the agent was down).
func resume(pos *Positions, path string, t *tailed, size int64) (int64, bool) {
	if p, ok := pos.Get(path); ok && t.same(p) && p.Offset <= size {
		return p.Offset, true
	}
	if p, ok := pos.Find(t); ok && p.Offset <= size {
		return p.Offset, true
	}
	return 0, false
}

// TailFiles follows every file matching src.Path, surviving rotation (rename + new file),
// renames inside the glob (app.log -> app.log.1 is not read twice) and truncation
// (copytruncate). Files present at the first scan start at their saved offset, or at the end
// unless start_position is "beginning"; files that appear later are read from the start.
//
// Superseded files (see superseded) are drained, closed and marked read to their end, then only
// stat'ed: a write to one makes it recent again and it is reopened at its saved offset, so
// releasing a file never loses a line.
func TailFiles(ctx context.Context, src config.Source, pos *Positions, tr *Tracker, out chan<- Line) error {
	open := map[string]*tailed{}
	defer func() {
		for _, t := range open {
			t.f.Close()
		}
	}()
	first := true
	tick := time.NewTicker(PollEvery)
	defer tick.Stop()
	for {
		matches, err := filepath.Glob(src.Path)
		tr.SetError(err)
		sort.Strings(matches)
		tr.SetFiles(matches)
		stats := map[string]os.FileInfo{}
		where := map[fileID]string{}
		for _, path := range matches {
			if fi, err := os.Stat(path); err == nil && !fi.IsDir() {
				stats[path] = fi
				where[idOf(fi)] = path
			}
		}
		// Re-key open files by where they are now, keeping reader and offset across a rename.
		moved := map[string]*tailed{}
		var left []string
		for path, t := range open {
			if np, ok := where[t.id]; ok {
				moved[np] = t
			} else {
				left = append(left, path)
			}
		}
		for _, path := range left {
			t := open[path]
			if _, taken := moved[path]; !taken {
				moved[path] = t // replaced in place or gone: handled below
				continue
			}
			if !drain(ctx, path, t, tr, out) {
				return nil
			}
			t.f.Close()
		}
		open = moved
		skip := superseded(stats, src.IgnoreOlder(), time.Now())
		var skipped []string
		for _, path := range matches {
			if skip[path] {
				skipped = append(skipped, path)
			}
		}
		tr.SetSkipped(skipped)

		for _, path := range matches {
			fi := stats[path]
			if fi == nil {
				continue
			}
			t := open[path]
			if id := idOf(fi); skip[path] && (t == nil || t.id == id) {
				if t != nil {
					if !drain(ctx, path, t, tr, out) {
						return nil
					}
					pos.Set(path, t.position())
					t.f.Close()
					delete(open, path)
				} else if p, ok := pos.Get(path); !ok || p.Inode != id.Ino || p.Dev != id.Dev {
					// Never read: count it as read to its end, so a later write sends only new lines.
					if t, err = openFile(path, fi); err == nil {
						if t.offset, ok = resume(pos, path, t, fi.Size()); !ok {
							t.offset = fi.Size()
						}
						pos.Set(path, t.position())
						t.f.Close()
					}
				}
				continue
			}
			if t != nil && t.id != idOf(fi) {
				// Rotated: drain the old file, then follow the new one from its start.
				if !drain(ctx, path, t, tr, out) {
					return nil
				}
				t.f.Close()
				t = nil
				delete(open, path)
				if t, err = openFile(path, fi); err != nil {
					tr.SetError(err)
					continue
				}
				open[path] = t
			}
			if t == nil {
				if t, err = openFile(path, fi); err != nil {
					tr.SetError(err)
					continue
				}
				off, ok := resume(pos, path, t, fi.Size())
				if !ok && first && src.StartAt != "beginning" {
					off = fi.Size()
				}
				if err := t.seek(off); err != nil {
					t.f.Close()
					tr.SetError(err)
					continue
				}
				open[path] = t
			}
			if fi.Size() < t.offset { // truncated in place: new content, new head
				t.seek(0)
				t.head, t.headLen = headOf(t.f, headSize)
			}
			if !drain(ctx, path, t, tr, out) {
				return nil
			}
			pos.Set(path, t.position())
		}
		for path, t := range open {
			if stats[path] == nil { // deleted or no longer matching
				drain(ctx, path, t, tr, out)
				t.f.Close()
				delete(open, path)
			}
		}
		pos.Prune(src.Path, matches) // after the loop: a renamed file was looked up by its old name
		first = false
		select {
		case <-ctx.Done():
			return nil
		case <-tick.C:
		}
	}
}

func openFile(path string, fi os.FileInfo) (*tailed, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	t := &tailed{f: f, r: bufio.NewReaderSize(f, 64*1024), id: idOf(fi)}
	t.head, t.headLen = headOf(f, headSize)
	return t, nil
}

func (t *tailed) seek(off int64) error {
	if _, err := t.f.Seek(off, io.SeekStart); err != nil {
		return err
	}
	t.r.Reset(t.f)
	t.offset, t.partial = off, nil
	return nil
}

// drain sends every complete line; a trailing partial line waits for its newline. The offset only
// moves past complete lines, so a restart re-reads a half-written line instead of splitting it.
func drain(ctx context.Context, path string, t *tailed, tr *Tracker, out chan<- Line) bool {
	for {
		chunk, err := t.r.ReadSlice('\n')
		if len(chunk) > 0 {
			t.partial = append(t.partial, chunk...)
		}
		if errors.Is(err, bufio.ErrBufferFull) && len(t.partial) < maxLine {
			continue
		}
		if err != nil && !errors.Is(err, bufio.ErrBufferFull) {
			return true // EOF: keep partial for the next poll
		}
		line := strings.TrimSuffix(string(t.partial), "\n")
		t.offset += int64(len(t.partial))
		t.partial = t.partial[:0]
		tr.Count(line)
		if !send(ctx, out, Line{Key: path, Text: line, Time: time.Now(), Tags: []string{"filename:" + filepath.Base(path)}}) {
			return false
		}
	}
}

var (
	seriesExt  = regexp.MustCompile(`(?i)\.(log|gz|txt|out)$`)
	seriesDate = regexp.MustCompile(`[-_.]?(\d{4}[-_.]?\d{2}[-_.]?\d{2}|\d{2}[-_.]\d{2}[-_.]\d{4})$`) // 2026-11-17, 20261117, 17-11-2026
	// shortcut: only 1-3 digit counters count as rotation, so server-8080.log stays its own series; widen if logs rotate past .999.
	seriesNum = regexp.MustCompile(`[-_.]\d{1,3}$`)
)

// SeriesName is a file's name with rotation dates, counters and log extensions stripped, so
// app.log, app.log.3, app.001.log, app-2026-11-17.log and app.log.1.gz are all "app". A name
// that is nothing but a date or a number (01.txt, 17-11-2026.txt) takes its directory's name.
func SeriesName(path string) string {
	name := filepath.Base(path)
	for {
		next := seriesExt.ReplaceAllString(name, "")
		next = seriesDate.ReplaceAllString(next, "")
		next = seriesNum.ReplaceAllString(next, "")
		if next == name {
			break
		}
		name = next
	}
	if strings.Trim(name, "0123456789-_.") == "" {
		return filepath.Base(filepath.Dir(path))
	}
	return name
}
