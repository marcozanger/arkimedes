---
status: draft          # draft | approved
repo: humanharness
slug: notes-edit
created: 2026-11-05
---
# Notes edit and delete

## Problem

Who needs what, and why now.

## Goal

The outcome and how we know it worked. What it does seen from outside: what the user sees, the flows. No files, functions or commands; a technical choice appears only as a decision.

## Decisions

Each choice a reasonable person could dispute or that is costly to undo, in this format:

- **Move the note to a trash folder**
  - Alternatives: delete the file for good; mark it deleted in a database.
  - Pros: the user can undo a delete.
  - Cons: the trash folder needs its own cleanup.
  - Why: deleting by mistake is the likeliest user error, and undo is cheap here.

## Design

Behaviour from outside: components, data flow, errors. Diagrams welcome (Mermaid). For a visual change, a before/after pair and a link to the mockup:

![Before: the notes list today](before.png)
![After: each note with a delete button](after.png)

[Open mockup](https://claude.ai/artifact/...)

Images sit next to spec.md in the desk folder.

## Testing

What proves it works.

## Out of scope

## Open questions

Must be empty before `status: approved`.
