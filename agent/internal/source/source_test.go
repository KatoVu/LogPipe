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

func TestTailFollowsLatestFileOfASeries(t *testing.T) {
	SwitchGrace = 150 * time.Millisecond
	defer func() { SwitchGrace = time.Minute }()
	dir := t.TempDir()
	day := func(d string) string { return filepath.Join(dir, d+"-11-2026.txt") }
	appendFile(t, day("19"), "archive\n")
	appendFile(t, day("20"), "today\n")
	appendFile(t, filepath.Join(dir, "a-b-c.txt"), "not in the series\n")
	pos := LoadPositions(filepath.Join(dir, "positions.json"))
	src := config.Source{Type: "file", Path: filepath.Join(dir, "{DD}-{MM}-{YYYY}.txt"), StartAt: "beginning"}
	tr := &Tracker{}
	ctx, cancel := context.WithCancel(context.Background())
	out := make(chan Line, 10)
	done := make(chan struct{})
	go func() { TailFiles(ctx, src, pos, tr, out); close(done) }()

	if got := collect(t, out, 1); got[0] != "today" {
		t.Fatalf("archive replayed or latest missed: %q", got)
	}
	noMore(t, out)
	if s := tr.Snapshot(src); !reflect.DeepEqual(s.Files, []string{day("20")}) || !reflect.DeepEqual(s.Skipped, []string{day("19"), filepath.Join(dir, "a-b-c.txt")}) {
		t.Fatalf("files %q skipped %q", s.Files, s.Skipped)
	}
	// the next day starts: it is read whole, and the previous day still catches a late flush
	appendFile(t, day("21"), "tomorrow\n")
	appendFile(t, day("20"), "late flush\n")
	got := collect(t, out, 2)
	sort.Strings(got)
	if !reflect.DeepEqual(got, []string{"late flush", "tomorrow"}) {
		t.Fatalf("switch-over: %q", got)
	}
	time.Sleep(300 * time.Millisecond) // past the grace: the old day is closed
	appendFile(t, day("20"), "after grace\n")
	noMore(t, out)
	if s := tr.Snapshot(src); !reflect.DeepEqual(s.Files, []string{day("21")}) {
		t.Fatalf("after grace files %q", s.Files)
	}
	cancel()
	<-done
	pos.Save()

	// the next day began while the agent was down: read whole, not from its end; files the last
	// run left unread (day 20 after its grace, day 21's tail) are finished
	appendFile(t, day("21"), "end of 21\n")
	appendFile(t, day("22"), "while down\n")
	pos2 := LoadPositions(filepath.Join(dir, "positions.json"))
	src.StartAt = ""
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	go TailFiles(ctx, src, pos2, &Tracker{}, out)
	got = collect(t, out, 3)
	sort.Strings(got)
	if !reflect.DeepEqual(got, []string{"after grace", "end of 21", "while down"}) {
		t.Fatalf("restart: %q", got)
	}
}

func TestTailLatestLowest(t *testing.T) {
	dir := t.TempDir()
	for _, n := range []string{"1", "2", "10"} {
		appendFile(t, filepath.Join(dir, "part-"+n+".log"), n+"\n")
	}
	src := config.Source{Type: "file", Path: filepath.Join(dir, "part-{N}.log"), StartAt: "beginning", Latest: "lowest"}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	out := make(chan Line, 10)
	go TailFiles(ctx, src, LoadPositions(filepath.Join(dir, "p.json")), &Tracker{}, out)
	if got := collect(t, out, 1); got[0] != "1" {
		t.Fatalf("got %q", got)
	}
	noMore(t, out)
}

func TestServiceName(t *testing.T) {
	for _, c := range [][3]string{
		{"/l/app-{YYYY}-{MM}-{DD}.log", "/l/app-2026-11-17.log", "app"},
		{"/l/myapp/{DD}-{MM}-{YYYY}.txt", "/l/myapp/20-11-2026.txt", "myapp"},
		{"/l/myapp/{N}.txt", "/l/myapp/01.txt", "myapp"},
		{"/l/app.log.{N}", "/l/app.log.3", "app"},
		{"/l/{YYYY}/{MM}/{DD}/api.log", "/l/2026/11/17/api.log", "api"},
		{"/l/*.log", "/l/worker.log", "worker"},
	} {
		if got := ServiceName(config.Source{Path: c[0]}, c[1]); got != c[2] {
			t.Errorf("ServiceName(%q) = %q, want %q", c[0], got, c[2])
		}
	}
}

func TestDetectFormat(t *testing.T) {
	for _, c := range []struct {
		files []string
		want  string
	}{
		{[]string{"01.txt", "02.txt", "10.txt"}, "{N}.txt"},
		{[]string{"20-11-2026.txt", "21-11-2026.txt"}, "{DD}-{MM}-{YYYY}.txt"},
		{[]string{"app-2026-11-17.log", "app-2026-11-18.log"}, "app-{YYYY}-{MM}-{DD}.log"},
		{[]string{"app_20261117.log"}, "app_{YYYY}{MM}{DD}.log"}, // a date is clear from one file
		{[]string{"app-2026-11-17-1.log", "app-2026-11-17-2.log", "app-2026-11-18-1.log"}, "app-{YYYY}-{MM}-{DD}-{N}.log"},
		{[]string{"v2-app.1.log", "v2-app.2.log"}, "v2-app.{N}.log"}, // digits that never change stay
		{[]string{"app.log"}, ""},
		{[]string{"01.txt"}, ""},               // one counter tells nothing
		{[]string{"a1-1.txt", "a2-2.txt"}, ""}, // two runs vary: not one series
		{[]string{"01.txt", "notes.txt"}, ""},  // another file shares the glob
	} {
		dir := t.TempDir()
		for _, f := range c.files {
			appendFile(t, filepath.Join(dir, f), "")
		}
		got, ok := DetectFormat(filepath.Join(dir, "*"))
		want := ""
		if c.want != "" {
			want = filepath.Join(dir, c.want)
		}
		if got != want || ok != (c.want != "") {
			t.Errorf("%v: got %q %v, want %q", c.files, got, ok, want)
		}
	}
}
