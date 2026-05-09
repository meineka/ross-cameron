# Brain Mirror

Spiegel der Claude-Memory-Files (Stand-Snapshot zum Zeitpunkt des Git-Commits).
Original-Speicherort: `~/.claude/projects/C--Users-Szymon/memory/`.

Diese Kopie macht das Repo self-contained: das gesamte Wissen, das in dieser Session
aufgebaut wurde — sowohl die Cameron-Constraints **als auch** der Meta-Kontext (was
Claude über den User, das Projekt, die Code-Style-Präferenzen weiß) — ist hier
versioniert.

## Inhalt

| Datei | Inhalt |
|---|---|
| `MEMORY.md` | Master-Index der Memory-Files |
| `project_ross_cameron.md` | Projekt-spezifische Notizen + Audit-Trail |
| `user_profile.md` | User-Profil (Trader/Developer, Windows 11, MT5-Setup) |
| `feedback_style.md` | Code-Style + Response-Präferenzen |

## Sync-Regel

Bei substantiellen Änderungen am Brain (Memory) → diese Files aktualisieren und commiten.
Beim Klonen des Repos auf eine neue Maschine: optional in `~/.claude/projects/.../memory/`
zurückkopieren, um Claude-Kontext zu rekonstruieren.
