"""The `claude -p` backend is tested against a fake `claude` executable that mimics Claude Code's JSON envelope."""
import json
import os
import stat
import sys

import pytest

from shotforge.cli import main

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shim")

FAKE = '''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if "--version" in a:
    print("fake 0"); sys.exit(0)
assert "-p" in a and "--output-format" in a and "--allowedTools" in a
assert a[a.index("--tools") + 1] == "Read" and "--strict-mcp-config" in a
assert "APP NAME" in sys.stdin.read() and not any("APP NAME" in x for x in a)   # prompt on stdin, not argv
state =os.environ["FAKE_STATE"]
n = int(open(state).read()) + 1 if os.path.exists(state) else 1
open(state, "w").write(str(n))
shots = ["01_home.png", "02_stats.png", "03_goals.png"]
count = 6 if n > 1 else 3          # first answer has the wrong number of slides -> retry path
slides = [{"shot": shots[i % 3], "role": "feature",
           "title": {"en": f"Title {i} *x*", "tr": f"Başlık {i} *x*"}, "subtitle": {"en": "sub", "tr": "alt"}}
          for i in range(count)]
out = {"design": {"category": "productivity", "dimension": "3d", "template": "prism3d", "template_reason": "fake",
                  "colors": {"background": "#F97316", "text": "#111111", "accent": "#0EA5E9"}, "count": count},
       "slides": slides}
print(json.dumps({"type": "result", "is_error": False, "result": "```json\\n" + json.dumps(out) + "\\n```"}))
'''


def test_claude_backend_with_retry(shots_dir, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "calls"))
    copy = tmp_path / "c.json"
    main(["plan", str(shots_dir), "--app", "Demo", "--ai", "claude", "--no-ask", "--count", "6", "--copy", str(copy)])
    d = json.loads(copy.read_text(encoding="utf-8"))
    assert (tmp_path / "calls").read_text() == "2"          # retried once
    assert d["generated_with"] == "claude" and len(d["slides"]) == 6
    assert d["design"]["template"] == "prism3d" and d["design"]["colors"]["accent"] == "#0ea5e9"


def test_user_background_overrides_claude_theme_clash(shots_dir, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "calls"))
    copy = tmp_path / "c.json"
    # Claude proposes the light template prism3d; the user's dark background must win
    main(["plan", str(shots_dir), "--app", "Demo", "--ai", "claude", "--no-ask", "--count", "6",
          "--bg-color", "#0B1026", "--copy", str(copy)])
    d = json.loads(copy.read_text(encoding="utf-8"))["design"]
    assert d["template"] == "neon3d" and d["colors"]["background"] == "#0b1026"
    assert "uyumsuz" in d["template_reason"]
