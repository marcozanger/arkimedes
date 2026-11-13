// Copied from ~/.claude/skills/spec-review/scripts/lib/strings.mjs on 2026-11-13; keep in line by hand.
export const STRINGS = {
  en: {
    lang: "en",
    spec: "Spec", plan: "Plan",
    review: "Review", sections: "Sections",
    progress: "{done} of {total} reviewed",
    how: "How to review: mark each section <b>OK</b> or <b>Change</b> and write what to change. At the end, one button copies all your comments.",
    aiTag: "AI", aiTitle: "Written by AI from the section text. Check it against the full text.",
    fullText: "Full text · {n} lines",
    lines: "lines {a}–{b}", line: "line {a}",
    decisions: "Decisions",
    flowFallback: "Flow (offline, cannot draw it):",
    kinds: { system: "system / code", ai: "AI", user: "person", decision: "decision" },
    question: "This section?", ok: "OK", change: "Change", notePh: "What would you change…",
    sendTitle: "Your comments", sendHelp: "Built from what you marked. Copy it and paste it into the session.",
    nothing: "Nothing marked yet.", copy: "Copy comments", copied: "Copied", selected: "Selected: copy with Cmd/Ctrl+C",
    sendHelpArtifact: "Built from what you marked. Send it to Claude and the session receives it and replies in the thread; or copy and paste it.",
    sendClaude: "Send to Claude", sending: "Sending…", sentClaude: "Sent: Claude replies in the comment thread",
    claudeOff: "To send to Claude, open this page with edit access while the Claude session is running. Meanwhile, copy it.",
    claudeNoSession: "No Claude session is listening to this page. Copy your comments and paste them into the session.",
    claudeFailed: "Could not send ({code}). Copy your comments and paste them into the session.",
    truncated: "… (cut: the rest is in Copy comments)",
    reviewOf: "Review of {title}", tagOk: "OK", tagChange: "CHANGE", tagNote: "note",
    intro: "Introduction",
    imgMissing: "Image not found: {path}",
    code: (lang, n) => `Code${lang ? " " + lang : ""} · ${n} lines`,
  },
};

export const fill = (t, vars) => t.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
