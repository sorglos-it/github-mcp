# Changelog

## 1.0.2 – 2026-09-23

- Repository, path, branch and commit can no longer leave the address they belong to.
  A `..` in any of them used to be removed on the way out, so `o/n/../../../user` ended
  up at a different endpoint entirely — at worst a branch was deleted in somebody else's
  repository. Owner and repository name are now checked against what GitHub itself allows,
  paths and refs are escaped, and `..` is refused.
- A `#` or `?` in a file name no longer cuts the path short: `a#b` was read — and written —
  as `a`. Both are escaped now, as are spaces and `%`.
- Writes no longer follow a redirect. GitHub answers 301 for a renamed repository, and a
  redirected POST turns into a GET: the write silently did nothing and still reported
  success. Reads follow redirects as before.
- Deleting a repository is refused straight away when the setting forbids it, without
  asking GitHub first. `push_files` checks every path before it writes the first file.

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
