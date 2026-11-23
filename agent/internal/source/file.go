package source

import (
	"bufio"
	"context"
	"errors"
	"hash/fnv"
	"io"
	"os"
	"path/filepath"
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
	since   time.Time // when partial started waiting for its newline
	head    uint64    // hash of the first headLen bytes, to tell a reused inode from the same file
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

// SwitchGrace is how long the previous file of a series is still tailed after a later one appears,
// for an app that is still flushing to it.
var SwitchGrace = time.Minute

// pickLatest keeps in stats only the files a placeholder source tails: the latest of the series,
// files within their grace after being replaced, and on the first scan files left unfinished by
// the last run. It returns the latest file.
func pickLatest(f *config.Format, lowest bool, stats map[string]os.FileInfo, pos *Positions, first bool, prev string, grace map[string]time.Time) string {
	now := time.Now()
	latest, best := "", ""
	for p := range stats {
		k, ok := f.Key(p)
		if !ok {
			continue
		}
		if latest == "" || k != best && (k > best) != lowest || k == best && p > latest {
			latest, best = p, k
		}
		if first {
			if sp, ok := pos.Get(p); ok && sp.Offset < stats[p].Size() {
				grace[p] = now.Add(SwitchGrace)
			}
		}
	}
	if prev != "" && prev != latest {
		grace[prev] = now.Add(SwitchGrace)
	}
	for p, until := range grace {
		if now.After(until) {
			delete(grace, p)
		}
	}
	for p := range stats {
		if _, ok := grace[p]; p != latest && !ok {
			delete(stats, p)
		}
	}
	return latest
}

// known reports whether any file of a series has a saved position, i.e. the agent followed it before.
func known(pos *Positions, matches []string) bool {
	for _, m := range matches {
		if _, ok := pos.Get(m); ok {
			return true
		}
	}
	return false
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
// A path with placeholders (config.Format) tails only the latest file of its series. When a later
// file appears, the previous one stays tailed for SwitchGrace to catch late writes, then is closed.
func TailFiles(ctx context.Context, src config.Source, pos *Positions, tr *Tracker, out chan<- Line) error {
	open := map[string]*tailed{}
	defer func() {
		for _, t := range open {
			t.f.Close()
		}
	}()
	f, _ := src.Format() // validated on load
	glob := src.Path
	if f != nil {
		glob = f.Glob
	}
	latest, grace := "", map[string]time.Time{}
	first := true
	tick := time.NewTicker(PollEvery)
	defer tick.Stop()
	for {
		matches, err := filepath.Glob(glob)
		tr.SetError(err)
		sort.Strings(matches)
		stats := map[string]os.FileInfo{}
		ids := map[fileID]bool{}
		for _, path := range matches {
			fi, err := os.Stat(path)
			if err != nil || fi.IsDir() {
				continue
			}
			if id := idOf(fi); id != (fileID{}) {
				if ids[id] { // a hard link to a file already matched: one reader per file
					continue
				}
				ids[id] = true
			}
			stats[path] = fi
		}
		if f != nil {
			latest = pickLatest(f, src.Latest == "lowest", stats, pos, first, latest, grace)
		}
		tracked, skipped := []string{}, []string{}
		for _, path := range matches {
			if stats[path] != nil {
				tracked = append(tracked, path)
			} else if f != nil {
				skipped = append(skipped, path)
			}
		}
		tr.SetFiles(tracked)
		tr.SetSkipped(skipped)
		where := map[fileID]string{}
		for path, fi := range stats {
			where[idOf(fi)] = path
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
		// Saved after the scan: a new app.log must not overwrite the entry that app.log.1, renamed
		// while the agent was stopped, is looked up by later in the same scan.
		saved := map[string]Position{}
		for _, path := range matches {
			fi := stats[path]
			if fi == nil {
				continue
			}
			t := open[path]
			if t != nil && t.id != idOf(fi) {
				// Rotated: drain the old file, then follow the new one from its start.
				if !drain(ctx, path, t, tr, out) {
					return nil
				}
				t.f.Close()
				t = nil
				delete(open, path)
				if t, fi, err = openFile(path); err != nil {
					tr.SetError(err)
					continue
				}
				open[path] = t
			}
			if t == nil {
				if t, fi, err = openFile(path); err != nil {
					tr.SetError(err)
					continue
				}
				off, ok := resume(pos, path, t, fi.Size())
				// A series the agent already followed: its new latest file is read whole, even
				// on the first scan after a restart.
				if !ok && first && src.StartAt != "beginning" && !(f != nil && known(pos, matches)) {
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
			saved[path] = t.position()
		}
		for path, p := range saved {
			pos.Set(path, p)
		}
		for path, t := range open {
			if stats[path] == nil { // deleted or no longer matching
				drain(ctx, path, t, tr, out)
				t.f.Close()
				delete(open, path)
			}
		}
		pos.Prune(glob, matches) // after the loop: a renamed file was looked up by its old name
		var oldest time.Time
		for _, t := range open {
			if len(t.partial) > 0 && (oldest.IsZero() || t.since.Before(oldest)) {
				oldest = t.since
			}
		}
		tr.Scanned(oldest)
		first = false
		select {
		case <-ctx.Done():
			return nil
		case <-tick.C:
		}
	}
}

// openFile opens path and describes what was opened, not what the path held a moment earlier,
// so a rotation between the scan's stat and this open cannot mix up two files.
func openFile(path string) (*tailed, os.FileInfo, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	fi, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, nil, err
	}
	t := &tailed{f: f, r: bufio.NewReaderSize(f, 64*1024), id: idOf(fi)}
	t.head, t.headLen = headOf(f, headSize)
	return t, fi, nil
}

func (t *tailed) seek(off int64) error {
	if _, err := t.f.Seek(off, io.SeekStart); err != nil {
		return err
	}
	t.r.Reset(t.f)
	t.offset, t.partial, t.since = off, nil, time.Time{}
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
			if len(t.partial) > 0 && t.since.IsZero() {
				t.since = time.Now()
			}
			return true // EOF: keep partial for the next poll
		}
		line := strings.TrimSuffix(string(t.partial), "\n")
		t.offset += int64(len(t.partial))
		t.partial, t.since = t.partial[:0], time.Time{}
		tr.Count(line)
		if !send(ctx, out, Line{Key: path, Text: line, Time: time.Now(), Tags: []string{"filename:" + filepath.Base(path)}}) {
			return false
		}
	}
}

// ServiceName names a file source that has no service: from the fixed start of its file name
// format (app-{YYYY}-{MM}-{DD}.log -> app), else the folder (myapp/{N}.txt -> myapp), else the
// file name (app.log -> app).
func ServiceName(src config.Source, path string) string {
	if config.HasFormat(src.Path) {
		path = src.Path
	}
	if abs, err := filepath.Abs(path); err == nil { // a relative 01.txt still has a folder
		path = abs
	}
	for p := path; p != "." && p != "/"; p = filepath.Dir(p) {
		name := strings.Trim(config.StaticName(p), "-_. ")
		name = strings.TrimSuffix(name, filepath.Ext(name))
		if name != "" && !config.HasFormat(name) {
			return name
		}
	}
	return filepath.Base(path)
}
