#!/usr/bin/env python3
"""Stand-in for the `ark` session commands, used only by checks C1-C6.

event <session> <what> [text...]   record a line
wait <session> <check>             block until `send <check>` delivers a message, then print it
send <check> <message...>          deliver a message (you run this)
log                                print what was recorded
"""
import os
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("ARK_CHECKS_HOME", Path.home() / "ark-checks"))
STATE = HOME / "state"
LOG = STATE / "events.log"


def record(*parts):
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(time.strftime("%F %T ") + " ".join(parts) + "\n")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "event" and len(args) >= 2:
        record(*args)
    elif cmd == "wait" and len(args) == 2:
        session, check = args
        message = STATE / "inbox" / check
        record(session, "wait started")
        while not message.exists():
            time.sleep(2)
        text = message.read_text().strip()
        message.unlink()
        record(session, "wait delivered", text)
        print(text)
    elif cmd == "send" and len(args) >= 2:
        inbox = STATE / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        tmp = inbox / f".{args[0]}.tmp"
        tmp.write_text(" ".join(args[1:]))
        tmp.replace(inbox / args[0])
        record("you", "send", *args)
    elif cmd == "log":
        print(LOG.read_text() if LOG.exists() else "(nothing recorded)", end="")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
