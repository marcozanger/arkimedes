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


def drive(until, cycles=400):
    from ark import loop
    for _ in range(cycles):
        loop.cycle()
        if until():
            return
        time.sleep(0.05)
    raise AssertionError(f"not reached; state: {state()}")


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
