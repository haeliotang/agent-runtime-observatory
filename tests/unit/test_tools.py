import pytest
from aro_runtime import ToolError, Workspace
from aro_runtime.tools import apply_patch, read_file, run_tests, summarize, web_fetch


def test_workspace_from_dir_includes_dotfiles(tmp_path):
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("hello")
    ws = Workspace.from_dir(tmp_path)
    assert set(ws.files) == {".env", "sub/a.txt"}


def test_read_file_missing_raises():
    with pytest.raises(ToolError, match="file not found"):
        read_file(Workspace(), {"path": "nope.txt"})


def test_apply_patch_replaces_once():
    ws = Workspace({"a.py": "x = 1\nx = 1\n"})
    apply_patch(ws, {"path": "a.py", "find": "x = 1", "replace": "x = 2"})
    assert ws.files["a.py"] == "x = 2\nx = 1\n"


def test_apply_patch_missing_pattern_raises():
    ws = Workspace({"a.py": "x = 1\n"})
    with pytest.raises(ToolError, match="pattern not found"):
        apply_patch(ws, {"path": "a.py", "find": "y = 9", "replace": "z"})


def test_run_tests_red_then_green():
    ws = Workspace(
        {
            "app.py": "def f():\n    # BUG: wrong\n    return 0\n",
            "test_app.py": "def test_f():\n    pass\n",
        }
    )
    assert "FAILED" in run_tests(ws, {})
    ws.files["app.py"] = "def f():\n    return 1\n"
    assert "all passed" in run_tests(ws, {})


def test_web_fetch_resolves_corpus_fixture():
    ws = Workspace({"corpus/docs.example.com/page.md": "content"})
    assert web_fetch(ws, {"url": "https://docs.example.com/page.md"}) == "content"
    with pytest.raises(ToolError, match="offline fixture missing"):
        web_fetch(ws, {"url": "https://docs.example.com/other.md"})


def test_summarize_is_deterministic():
    ws = Workspace({"doc.md": "# Title\n\nsome body text here\n"})
    assert summarize(ws, {"path": "doc.md"}) == summarize(ws, {"path": "doc.md"})
    assert "# Title" in summarize(ws, {"path": "doc.md"})
