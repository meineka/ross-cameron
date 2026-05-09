---
name: Coding style and response preferences
description: How Szymon wants Claude to behave — code style, verbosity, tool use
type: feedback
originSessionId: 17f65d65-0f88-4785-87c3-43b59b9179fd
---
**Keep responses terse** — Szymon reads the code/diff directly. No trailing summaries restating what was done.

**Why**: User works fast and finds verbose recap text noisy.
**How to apply**: End turns with at most 1-2 sentences on what changed and what's next.

---

**Windows PowerShell, not bash** — All shell commands must use PowerShell syntax.

**Why**: Windows 11 environment; bash commands like `tail`, `&&` chaining, `$VAR` don't work.
**How to apply**: Use `Select-Object -Last N` instead of `tail`, `;` instead of `&&`, `$env:VAR`.

---

**Always use `if __name__ == '__main__':` in Python entry scripts** that use multiprocessing.

**Why**: Windows uses spawn (not fork) for multiprocessing; without the guard, worker processes
re-import __main__ and try to re-run the tournament, causing `RuntimeError: BrokenProcessPool`.
**How to apply**: Any script that calls `ProcessPoolExecutor` or `multiprocessing` must have this guard.

---

**German communication, English code** — User messages may be in German; all code, docs, and CLAUDE.md stay in English.
