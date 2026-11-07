import os
from pathlib import Path

from ark import inbox, loop, paths

CODE = Path(__file__).resolve().parents[1]


def test_items_come_back_in_order_and_go_when_done(ark_home):
    inbox.post("muscleapp/mobile", "s", "answer", id="q1", text="one")
    inbox.post("muscleapp/mobile", "s", "steer", job="a", text="two")
    items = inbox.pending("muscleapp/mobile", "s")
    assert [(i["kind"], i["text"]) for i in items] == [("answer", "one"), ("steer", "two")]
    inbox.done(items[0])
    assert [i["kind"] for i in inbox.pending("muscleapp/mobile", "s")] == ["steer"]
    assert inbox.pending("muscleapp/mobile", "other") == []


def test_what_cannot_be_read_comes_back_unreadable_and_can_be_cleared(ark_home):
    d = inbox.folder("r", "s")
    d.mkdir(parents=True)
    (d / "1-a.json").write_text("not json")
    (d / "2-b.json").write_text("[1]")
    (d / "3-c.json").symlink_to("/etc/hosts")
    os.mkfifo(d / "4-d.json")
    items = inbox.pending("r", "s")
    assert [i["kind"] for i in items] == ["unreadable"] * 4
    for i in items:
        inbox.done(i)
    assert list(d.iterdir()) == []


def test_drop_all(ark_home):
    inbox.post("r", "s", "answer", id="q1", text="x")
    inbox.drop_all("r", "s")
    assert inbox.pending("r", "s") == []
    inbox.drop_all("r", "never")


def test_a_new_item_changes_the_signature(ark_home):
    paths.approvals_dir().mkdir(parents=True)
    paths.runs_dir().mkdir(parents=True)
    before = loop.signature()
    inbox.post("r", "s", "answer", id="q1", text="x")
    assert loop.signature() != before


def test_the_inbox_is_not_tracked():
    assert "inbox/" in (CODE / ".gitignore").read_text().split()


def test_a_lone_surrogate_can_be_posted(ark_home):
    inbox.post("r", "s", "answer", id="q1", text="\udcff")
    assert inbox.pending("r", "s")[0]["text"] == "\udcff"
