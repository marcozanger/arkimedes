// Run: node tests/ui_logic.mjs ui/index.html
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(process.argv[2], "utf8");
const START = "// ---- logic: start ----", END = "// ---- logic: end ----";
const code = html.split(START)[1].split(END)[0];
const { decide, titleFor, readPlan, segments, etaText, spendText, flagOf, parseRoute, dur } =
  new Function(code + "; return {decide, titleFor, readPlan, segments, etaText, spendText, flagOf, parseRoute, dur};")();

const fresh = () => ({ lastAlert: null, loopDown: false, stale: false, done: [], unread: [] });
const ok = { stale: false, error: "" };
const alert = (id, repo = "r", slug = "s", text = `alert ${id}`) => ({ id, repo, slug, text, critical: false, job: "" });
const snap = (alerts = [], over = {}) => ({ version: 1, loop: { alive: true }, alerts, plans: [], ...over });

// 1. first load: nothing, lastAlert = max id
{
  const { notes, mem } = decide(fresh(), snap([alert(3), alert(7)], { plans: [{ repo: "r", slug: "d", status: "done" }] }), ok);
  assert.equal(notes.length, 0);
  assert.equal(mem.lastAlert, 7);
  assert.deepEqual(mem.done, ["r/d"]);
  assert.equal(decide(fresh(), snap(), ok).mem.lastAlert, 0);
}

// 2. one new alert: one note with its plan, title starts with (1)
{
  let { mem } = decide(fresh(), snap([alert(1)]), ok);
  const r = decide(mem, snap([alert(1), alert(2, "r", "s", "x".repeat(300))]), ok);
  assert.equal(r.notes.length, 1);
  assert.equal(r.notes[0].title, "r/s");
  assert.equal(r.notes[0].plan, "r/s");
  assert.equal(r.notes[0].body.length, 200);
  assert.ok(titleFor(r.mem, snap(), ok).startsWith("(1) "));
  assert.equal(decide(r.mem, snap([alert(1), alert(2)]), ok).notes.length, 0);
}

// 3. five new alerts: one summary
{
  const { mem } = decide(fresh(), snap(), ok);
  const r = decide(mem, snap([1, 2, 3, 4, 5].map(i => alert(i))), ok);
  assert.equal(r.notes.length, 1);
  assert.equal(r.notes[0].title, "Arkimedes");
  assert.equal(r.notes[0].body, "5 new alerts");
  assert.equal(r.notes[0].plan, null);
  assert.equal(r.mem.unread.length, 5);
}

// 4. loop down twice: one note; alive then down again: one more
{
  const down = snap([], { loop: { alive: false } });
  let { mem } = decide(fresh(), snap(), ok);
  let r = decide(mem, down, ok);
  assert.equal(r.notes.length, 1);
  assert.equal(r.notes[0].title, "ark-loop is not running");
  assert.ok(titleFor(r.mem, down, ok).startsWith("Loop down · "));
  r = decide(r.mem, down, ok);
  assert.equal(r.notes.length, 0);
  r = decide(r.mem, snap(), ok);
  assert.equal(r.notes.length, 0);
  r = decide(r.mem, down, ok);
  assert.equal(r.notes.length, 1);
}

// 5. a plan turning done with a final PR: one note with its url, then none
{
  const running = snap([], { plans: [{ repo: "r", slug: "p", status: "running" }] });
  const done = snap([], { plans: [{ repo: "r", slug: "p", status: "done", final_pr: { url: "https://example.com/pr/1" } }] });
  let { mem } = decide(fresh(), running, ok);
  let r = decide(mem, done, ok);
  assert.equal(r.notes.length, 1);
  assert.equal(r.notes[0].title, "r/p is done");
  assert.equal(r.notes[0].url, "https://example.com/pr/1");
  assert.equal(decide(r.mem, done, ok).notes.length, 0);
}

// 6. stale twice: one note; while stale a new alert in the same call is not notified
{
  let { mem } = decide(fresh(), snap(), ok);
  const bad = { stale: true, error: "cannot reach ark ui" };
  let r = decide(mem, null, bad);
  assert.equal(r.notes.length, 1);
  assert.equal(r.notes[0].title, "Control center: stale data");
  assert.equal(r.notes[0].body, "cannot reach ark ui");
  assert.ok(titleFor(r.mem, null, bad).startsWith("Stale · "));
  r = decide(r.mem, snap([alert(9)]), bad);
  assert.equal(r.notes.length, 0);
  assert.equal(r.mem.lastAlert, 0);
  r = decide(r.mem, snap([alert(9)]), ok);
  assert.equal(r.mem.stale, false);
  assert.equal(r.notes.length, 1);
}

// 7. readPlan removes only that plan's unread alerts
{
  const mem = { ...fresh(), unread: [{ id: 1, plan: "a/b" }, { id: 2, plan: "c/d" }, { id: 3, plan: null }] };
  assert.deepEqual(readPlan(mem, "a/b").unread.map(u => u.id), [2, 3]);
}

// 8. stage bar: one segment per job, coloured by its status
{
  const jobs = ["merged", "working", "reviewing", "waiting_answer", "attached", "blocked", "pending", "waiting_dep", "waiting_fix"]
    .map(status => ({ id: status, status }));
  assert.deepEqual(segments({ jobs }), ["merged", "active", "active", "stuck", "stuck", "blocked", "", "", "active"]);
  assert.deepEqual(segments({ jobs: [] }), []);
}

// 9. ETA text
{
  assert.equal(dur(80), "1h20m");
  assert.equal(dur(120), "2h");
  assert.equal(dur(15), "15m");
  const run = (left_min, delay_min) => ({ status: "running", times: { left_min, delay_min } });
  assert.equal(etaText(run(80, 15)), "~1h20m left · 15m late");
  assert.equal(etaText(run(80, 1)), "~1h20m left · on time");
  assert.equal(etaText(run(30, -10)), "~30m left · 10m early");
  assert.equal(etaText(run(null, 0)), "Not estimated");
  assert.equal(etaText({ status: "done", times: { left_min: 0, delay_min: 5 } }), "Finished · 5m late");
  assert.equal(etaText({ status: "waiting", times: { left_min: 95, delay_min: 0 } }), "Not started · ~1h35m of work");
  assert.equal(etaText({ status: "held", times: { left_min: 40, delay_min: 0 } }), "Paused");
}

// 10. spend text
{
  assert.equal(spendText({ usd: 4.2, budget_usd: 14 }), "$4.20 of $14");
  assert.equal(spendText({ usd: 15.1, budget_usd: 14 }), "$15.10 of $14 · over");
  assert.equal(spendText({ usd: 0, budget_usd: 9.5 }), "$0.00 of $9.50");
  assert.equal(spendText(null), "");
}

// 11. the flag: Blocked beats Waits for you
{
  const job = status => ({ id: status, status });
  assert.equal(flagOf({ status: "running", jobs: [job("working")], questions: [] }), "");
  assert.equal(flagOf({ status: "running", jobs: [job("waiting_answer")], questions: [] }), "Waits for you");
  assert.equal(flagOf({ status: "running", jobs: [job("working")], questions: [{ id: "q1" }] }), "Waits for you");
  assert.equal(flagOf({ status: "held", jobs: [], questions: [] }), "Waits for you");
  assert.equal(flagOf({ status: "running", jobs: [job("blocked"), job("waiting_answer")], questions: [] }), "Blocked");
  assert.equal(flagOf({ status: "blocked", jobs: [], questions: [] }), "Blocked");
  assert.equal(flagOf({ status: "done", jobs: [job("merged")], questions: [] }), "");
}

// 12. routes
{
  assert.deepEqual(parseRoute(""), { screen: "home" });
  assert.deepEqual(parseRoute("#/"), { screen: "home" });
  assert.deepEqual(parseRoute("#/history"), { screen: "history" });
  assert.deepEqual(parseRoute("#/euklides"), { screen: "euklides" });
  assert.deepEqual(parseRoute("#/plan/arkimedes/ui-polish/questions"), { screen: "plan", repo: "arkimedes", slug: "ui-polish", tab: "questions" });
  assert.deepEqual(parseRoute("#/plan/muscleapp/mobile/onboarding-copy/spec"), { screen: "plan", repo: "muscleapp", slug: "mobile/onboarding-copy", tab: "spec" });
  assert.deepEqual(parseRoute("#/plan/r/s"), { screen: "plan", repo: "r", slug: "s", tab: "timeline" });
  assert.deepEqual(parseRoute("#/draft/r/s"), { screen: "draft", repo: "r", slug: "s" });
  assert.deepEqual(parseRoute("#/nowhere"), { screen: "home" });
  assert.deepEqual(parseRoute("#/plan/r%2"), { screen: "home" });
}

console.log("ui logic ok");
