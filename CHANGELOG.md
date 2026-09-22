# Changelog

## 1.0.1 – 2026-09-23

- `GitHubError` is a `ToolError`: its text reaches Claude instead of a bare "Error executing tool".
  Everything the server refuses on purpose — no repository given, a confirmation that does not match,
  the default branch that cannot be deleted, a rejected request from GitHub — is now readable, so
  Claude can say what to do next.
- Author is now „Sorglos Thomas Weirich“. Claude Desktop derives the extension's identity from it:
  uninstall the old extension once before installing this version, then enter the settings again.
- Project layout follows the project standard: the server lives in `apps/server/` (`server.py`, `manifest.json`,
  `assets/icon.png`, `VERSION`).
- Inside the `.mcpb`, `server.py` now sits at the top level and the icon in `assets/`; settings and tools are
  unchanged.
- `tools/build.py` builds `dist/github-<version>.mcpb` with Python alone — no Node.js or `npx` needed any more. The
  bundle now also carries `LICENSE`.
- The ready-made `.mcpb` is no longer kept in the repository; it is downloaded from Releases.
- README rewritten: start in 3 steps, paths for the new layout; the troubleshooting command finds `server.py` in old
  and new installations alike.

## 1.0.0 – 2026-07-31

- First release: 19 tools for repositories, files, branches and commits on github.com.
- New repositories are private by default; deleting whole repositories stays switched off unless allowed in the
  settings.
