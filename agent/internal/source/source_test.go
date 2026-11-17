package source

import (
	"bytes"
	"context"
	"encoding/binary"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"

	"github.com/jakekato/logpipe/agent/internal/config"
)

func init() { PollEvery = 10 * time.Millisecond }

func collect(t *testing.T, out <-chan Line, n int) []string {
	t.Helper()
	var got []string
	timeout := time.After(3 * time.Second)
	for len(got) < n {
		select {
		case l := <-out:
			got = append(got, l.Text)
		case <-timeout:
			t.Fatalf("timed out after %q", got)
		}
	}
	return got
}

func appendFile(t *testing.T, path, s string) {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	f.WriteString(s)
	f.Close()
}

func TestTailRotationTruncationAndResume(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "app.log")
	appendFile(t, path, "old line before start\n")
	pos := LoadPositions(filepath.Join(dir, "positions.json"))
	src := config.Source{Type: "file", Path: filepath.Join(dir, "*.log")}
	ctx, cancel := context.WithCancel(context.Background())
	out := make(chan Line, 100)
	done := make(chan struct{})
	go func() { TailFiles(ctx, src, pos, &Tracker{}, out); close(done) }()

	time.Sleep(50 * time.Millisecond)
	appendFile(t, path, "a\npart")
	if got := collect(t, out, 1); got[0] != "a" {
		t.Fatalf("start_position end not honoured: %q", got)
	}
	appendFile(t, path, "ial\n")
	if got := collect(t, out, 1); got[0] != "partial" {
		t.Fatalf("partial line split: %q", got)
	}
	// rotate: rename + new file
	os.Rename(path, path+".1")
	appendFile(t, path, "after rotate\n")
	if got := collect(t, out, 1); got[0] != "after rotate" {
		t.Fatalf("rotation: %q", got)
	}
	// truncate in place
	os.Truncate(path, 0)
	time.Sleep(50 * time.Millisecond)
	appendFile(t, path, "x\n")
	if got := collect(t, out, 1); got[0] != "x" {
		t.Fatalf("truncation: %q", got)
	}
	cancel()
	<-done
	pos.Save()

	// restart: resumes at the saved offset, no resend
	appendFile(t, path, "while down\n")
	pos2 := LoadPositions(filepath.Join(dir, "positions.json"))
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	go TailFiles(ctx, src, pos2, &Tracker{}, out)
	if got := collect(t, out, 1); got[0] != "while down" {
		t.Fatalf("resume: %q", got)
	}
}

func TestDockerFrames(t *testing.T) {
	var buf bytes.Buffer
	frame := func(stream byte, s string) {
		hdr := make([]byte, 8)
		hdr[0] = stream
		binary.BigEndian.PutUint32(hdr[4:], uint32(len(s)))
		buf.Write(hdr)
		buf.WriteString(s)
	}
	frame(1, "2024-01-01T00:00:00.000000000Z hello\n2024-01-01T00:00:01Z wor")
	frame(2, "2024-01-01T00:00:02Z err\n")
	frame(1, "ld\n")
	var got []string
	readDockerStream(&buf, false, func(stream, text string) bool {
		_, msg := splitDockerTime(text)
		got = append(got, stream+":"+msg)
		return true
	})
	want := []string{"stdout:hello", "stderr:err", "stdout:world"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q", got)
	}
}

func TestParseSyslog(t *testing.T) {
	msg, app, tags := parseSyslog("<34>Oct 11 22:14:15 gw su[12]: 'su root' failed")
	if app != "su" || msg != "Oct 11 22:14:15 gw su[12]: 'su root' failed" || tags[0] != "severity:crit" {
		t.Fatalf("3164: %q %q %q", msg, app, tags)
	}
	msg, app, _ = parseSyslog("<165>1 2003-10-11T22:14:15.003Z host evntslog - ID47 [exampleSDID@32473 iut=\"3\"] An application event")
	if app != "evntslog" || msg != "An application event" {
		t.Fatalf("5424: %q %q", msg, app)
	}
}

func TestParseJournal(t *testing.T) {
	l, cursor, ok := parseJournal([]byte(`{"MESSAGE":"hi","_SYSTEMD_UNIT":"nginx.service","SYSLOG_IDENTIFIER":"nginx","__CURSOR":"c1","__REALTIME_TIMESTAMP":"1700000000000000"}`))
	if !ok || l.Text != "hi" || l.Service != "nginx" || cursor != "c1" || l.Time.Unix() != 1700000000 {
		t.Fatalf("got %+v %q", l, cursor)
	}
	if _, _, ok := parseJournal([]byte(`{"MESSAGE":[1,2]}`)); ok {
		t.Fatal("binary message should be skipped")
	}
}

func TestSeriesName(t *testing.T) {
	for in, want := range map[string]string{
		"/var/log/app.log": "app", "app.log.3": "app", "app.001.log": "app", "app-2026-11-17.log": "app",
		"app_20261117.log": "app", "app.log.1.gz": "app", "app-2026-11-17-01.log": "app",
		"server-8080.log": "server-8080", "error.log": "error", "syslog": "syslog", "app-17-11-2026.log": "app",
		"/var/log/myapp/01.txt": "myapp", "/var/log/myapp/002.txt": "myapp", "/var/log/myapp/20-11-2026.txt": "myapp",
		"/var/log/myapp/2026-11-17.log": "myapp", "/var/log/myapp/20261117.txt": "myapp",
	} {
		if got := SeriesName(in); got != want {
			t.Errorf("SeriesName(%q) = %q, want %q", in, got, want)
		}
	}
}

func noMore(t *testing.T, out <-chan Line) {
	t.Helper()
	select {
	case l := <-out:
		t.Fatalf("unexpected extra line %q from %s", l.Text, l.Key)
	case <-time.After(100 * time.Millisecond):
	}
}

func TestTailRenameInsideGlobIsNotReadTwice(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "app.log")
	appendFile(t, path, "")
	pos := LoadPositions(filepath.Join(dir, "positions.json"))
	src := config.Source{Type: "file", Path: path + "*"}
	ctx, cancel := context.WithCancel(context.Background())
	out := make(chan Line, 100)
	done := make(chan struct{})
	go func() { TailFiles(ctx, src, pos, &Tracker{}, out); close(done) }()

	time.Sleep(50 * time.Millisecond)
	appendFile(t, path, "one\n")
	collect(t, out, 1)
	os.Rename(path, path+".1")
	appendFile(t, path+".1", "late write to old file\n")
	appendFile(t, path, "two\n")
	got := collect(t, out, 2)
	if len(got) != 2 || !(got[0] == "late write to old file" && got[1] == "two" || got[1] == "late write to old file" && got[0] == "two") {
		t.Fatalf("got %q", got)
	}
	noMore(t, out)
	cancel()
	<-done
	pos.Save()

	// renamed again while the agent is down: resumes under the new name, nothing resent
	os.Rename(path+".1", path+".2")
	appendFile(t, path+".2", "while down\n")
	pos2 := LoadPositions(filepath.Join(dir, "positions.json"))
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	go TailFiles(ctx, src, pos2, &Tracker{}, out)
	if got := collect(t, out, 1); got[0] != "while down" {
		t.Fatalf("resume after rename: %q", got)
	}
	noMore(t, out)
}

func TestTailReusedInodeIsANewFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "app.log")
	appendFile(t, path, "fresh\n")
	fi, _ := os.Stat(path)
	id := idOf(fi)
	pos := LoadPositions(filepath.Join(dir, "positions.json"))
	// what an older, deleted file with the same inode left behind
	pos.Set(path, Position{Dev: id.Dev, Inode: id.Ino, Offset: 6, Head: 1, HeadLen: 6})
	src := config.Source{Type: "file", Path: path, StartAt: "beginning"}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	out := make(chan Line, 10)
	go TailFiles(ctx, src, pos, &Tracker{}, out)
	if got := collect(t, out, 1); got[0] != "fresh" {
		t.Fatalf("reused inode skipped the new file: %q", got)
	}
}

func TestSuperseded(t *testing.T) {
	dir := t.TempDir()
	now := time.Now()
	age := map[string]time.Duration{
		"app-2026-11-15.log": 72 * time.Hour, // older sibling, quiet long enough: released
		"app-2026-11-16.log": 2 * time.Hour,  // older sibling, but recently written: kept
		"app-2026-11-17.log": 0,
		"error.log":          240 * time.Hour, // quiet for days but alone in its series: kept
	}
	stats := map[string]os.FileInfo{}
	for name, a := range age {
		p := filepath.Join(dir, name)
		appendFile(t, p, "x\n")
		os.Chtimes(p, now.Add(-a), now.Add(-a))
		stats[p], _ = os.Stat(p)
	}
	got := superseded(stats, 24*time.Hour, now)
	if len(got) != 1 || !got[filepath.Join(dir, "app-2026-11-15.log")] {
		t.Fatalf("got %v", got)
	}
	if got := superseded(stats, 0, now); len(got) != 0 {
		t.Fatalf("ignore_older_than 0 released %v", got)
	}
}

func TestTailReleasesSupersededFilesWithoutLosingLines(t *testing.T) {
	dir := t.TempDir()
	old, cur, lone := filepath.Join(dir, "app-2026-11-15.log"), filepath.Join(dir, "app-2026-11-17.log"), filepath.Join(dir, "error.log")
	for _, p := range []string{old, cur, lone} {
		appendFile(t, p, filepath.Base(p)+"\n")
	}
	week := time.Now().Add(-7 * 24 * time.Hour)
	os.Chtimes(old, week, week)
	os.Chtimes(lone, week, week)
	pos := LoadPositions(filepath.Join(dir, "positions.json"))
	src := config.Source{Type: "file", Path: filepath.Join(dir, "*.log"), StartAt: "beginning"}
	tr := &Tracker{}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	out := make(chan Line, 10)
	go TailFiles(ctx, src, pos, tr, out)

	got := collect(t, out, 2)
	sort.Strings(got)
	if !reflect.DeepEqual(got, []string{"app-2026-11-17.log", "error.log"}) {
		t.Fatalf("archive replayed or live file dropped: %q", got)
	}
	noMore(t, out)
	if s := tr.Snapshot(src); !reflect.DeepEqual(s.Skipped, []string{old}) {
		t.Fatalf("skipped_files = %q", s.Skipped)
	}
	// a released file that is written again is picked up from where it was, nothing resent
	appendFile(t, old, "late\n")
	if got := collect(t, out, 1); got[0] != "late" {
		t.Fatalf("late write: %q", got)
	}
	// deleted files leave positions.json
	os.Remove(old)
	time.Sleep(50 * time.Millisecond)
	if _, ok := pos.Get(old); ok {
		t.Fatal("position of a deleted file kept")
	}
}

func TestSupersededDateOnlyAndNumberedNames(t *testing.T) {
	now := time.Now()
	for _, names := range [][2]string{{"20-11-2026.txt", "21-11-2026.txt"}, {"01.txt", "02.txt"}} {
		dir := filepath.Join(t.TempDir(), "myapp")
		os.Mkdir(dir, 0o755)
		stats := map[string]os.FileInfo{}
		for i, n := range names {
			p := filepath.Join(dir, n)
			appendFile(t, p, "x\n")
			if i == 0 {
				os.Chtimes(p, now.Add(-48*time.Hour), now.Add(-48*time.Hour))
			}
			stats[p], _ = os.Stat(p)
		}
		if got := superseded(stats, 24*time.Hour, now); len(got) != 1 || !got[filepath.Join(dir, names[0])] {
			t.Errorf("%v: got %v", names, got)
		}
	}
}
