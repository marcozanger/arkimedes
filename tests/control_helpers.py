"""What the control tests share. Imports of ark stay inside the functions: conftest imports helpers first."""
import time

SLUG = "notes-edit"


def state(repo="demo", slug=SLUG):
    from ark import run
    return run.load(repo, slug)


def job_status(name, repo="demo", slug=SLUG):
    st = state(repo, slug)
    return st["jobs"].get(name, {}).get("status") if st else None


def settled(repo="demo", slug=SLUG):
    from ark import paths, proc, run
    st = state(repo, slug)
    return bool(st and st["jobs"]) and all(j["status"] in run.TERMINAL for j in st["jobs"].values()) \
        and not proc.active(paths.run_dir(repo, slug))


def drive(until, seconds=60, what=None):
    """Run loop cycles until `until()` holds. Stops by a time limit, not a count of cycles: a busy machine
    runs fewer cycles in the same time. The failure names what it waited for and the state it ended in."""
    import inspect
    from ark import loop
    end = time.monotonic() + seconds
    cycles = 0
    while True:
        loop.cycle()
        cycles += 1
        if until():
            return
        if time.monotonic() > end:
            break
        time.sleep(0.05)
    if what is None:
        try:
            what = inspect.getsource(until).strip()
        except (OSError, TypeError):
            what = getattr(until, "__qualname__", repr(until))
    raise AssertionError(f"drive gave up after {seconds}s and {cycles} cycles waiting for: {what}\nstate: {state()}")


def change_state(**fields_by_job):
    """Rewrite job fields on disk, between cycles: the way a test moves a clock it cannot wait for."""
    from ark import run
    st = state()
    for job, fields in fields_by_job.items():
        st["jobs"][job].update(fields)
    run.save(st)


def ark(capsys, *argv):
    from ark import cli
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def workspace(ark_home, tmp_path, **limits):
    """Limits as given, and a notifier that writes to a file. Returns that file."""
    import json
    sent = tmp_path / "sent"
    f = ark_home / "workspace.json"
    ws = json.loads(f.read_text())      # its roots are what makes the demo repo valid
    ws["limits"] = {**ws.get("limits", {}), "load_max": 0, **limits}
    ws["notify"] = ["sh", "-c", f'printf "%s\\n" "$1" >> "{sent}"', "notify"]
    f.write_text(json.dumps(ws))
    return sent


def said(sent):
    return sent.read_text() if sent.exists() else ""
