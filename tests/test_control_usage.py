import json
import os

from ark import usage


def assistant(id, text, input=0, create=0, read=0, output=0):
    return json.dumps({"type": "assistant", "message": {
        "id": id, "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input, "cache_creation_input_tokens": create,
                  "cache_read_input_tokens": read, "output_tokens": output}}}) + "\n"


def test_counts_each_message_once_without_cache_reads(tmp_path):
    log = tmp_path / "a.log"
    log.write_text(assistant("m1", "one", input=10, create=100, read=9999, output=5)
                   + assistant("m1", "one more", input=10, create=100, read=9999, output=7)
                   + assistant("m2", "two", input=1, output=1))
    seen = usage.scan(log)
    assert seen["tokens"] == 10 + 100 + 7 + 2
    assert seen["last"] == "two"


def test_scan_is_incremental_and_waits_for_a_whole_line(tmp_path):
    log = tmp_path / "b.log"
    line = assistant("m1", "hello", output=4)
    log.write_text(line[:10])
    assert usage.scan(log)["tokens"] == 0
    with open(log, "a") as f:
        f.write(line[10:])
    assert usage.scan(log) == {"tokens": 4, "last": "hello"}


def test_a_new_file_at_the_same_path_starts_over(tmp_path):
    log = tmp_path / "c.log"
    log.write_text(assistant("m1", "old", output=50))
    assert usage.scan(log)["tokens"] == 50
    log.rename(tmp_path / "c.archived")   # what proc.consume does between two attempts
    log.write_text(assistant("m9", "new", output=3))
    assert usage.scan(log) == {"tokens": 3, "last": "new"}


def test_garbage_a_link_and_a_fifo_read_as_nothing(tmp_path):
    log = tmp_path / "d.log"
    log.write_text("not json\n5\n[]\n" + json.dumps({"type": "assistant", "message": "x"}) + "\n")
    assert usage.scan(log) == {"tokens": 0, "last": ""}
    secret = tmp_path / "secret"
    secret.write_text(assistant("m1", "secret", output=1))
    (tmp_path / "link.log").symlink_to(secret)
    assert usage.scan(tmp_path / "link.log") == {"tokens": 0, "last": ""}
    os.mkfifo(tmp_path / "fifo.log")
    assert usage.scan(tmp_path / "fifo.log") == {"tokens": 0, "last": ""}
    assert usage.scan(tmp_path / "missing.log") == {"tokens": 0, "last": ""}


def test_last_message_is_cleaned_and_short(tmp_path):
    log = tmp_path / "e.log"
    log.write_text(assistant("m1", "\x1b[31mred\x07 " + "x" * 2000, output=1))
    last = usage.scan(log)["last"]
    assert "\x1b" not in last and "\x07" not in last and len(last) == usage.LAST_MESSAGE


def test_render_line():
    assert usage.render_line(assistant("m", "hi\x1b[0m")) == "hi[0m"
    tool = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}})
    assert usage.render_line(tool) == '→ Bash {"command": "ls"}'
    done = json.dumps({"type": "result", "total_cost_usd": 0.5, "is_error": False})
    assert usage.render_line(done) == "── ended · $0.50"
    assert usage.render_line(json.dumps({"type": "user", "message": {}})) is None
    assert usage.render_line("plain \x1b text") == "plain  text"
