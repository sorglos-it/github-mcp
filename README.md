# github-mcp

[![Claude Desktop](https://img.shields.io/badge/Claude%20Desktop-extension-d97757.svg)](#)
[![API](https://img.shields.io/badge/api-GitHub%20REST-181717.svg?logo=github&logoColor=white)](#)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776ab.svg?logo=python&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Donate](https://img.shields.io/badge/Donate-PayPal-00457C.svg?logo=paypal)](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)

Claude Desktop extension that gives Claude access to **your repositories on github.com** — create them, describe them, browse and search the code, write files, commit, delete. Ships as a single `.mcpb` file.

The scope is deliberately **repositories and their contents**. Issues, pull requests, Actions and releases are not included; they can be added later as their own tools without rebuilding the package.

Responses are trimmed on the way out. A single repository object from the GitHub API carries well over a hundred fields, of which a handful matter — the rest would only crowd out the model's context. Binary files come back with a size instead of their content.

See also **[synology-calendar-mcp](https://github.com/sorglos-it/synology-calendar-mcp)** and **[synology-contacts-mcp](https://github.com/sorglos-it/synology-contacts-mcp)** — the same idea for calendars and contacts on a Synology NAS.

## Features

- **Repositories** — create (private or public, with `.gitignore` and licence template), list, search, rename, archive; change description, homepage, topics, visibility and default branch
- **Read** — list files and folders, read a file, read the README, search code across GitHub or inside one repository
- **Write** — create, overwrite and delete files with a commit message; `push_files` puts several changes into a **single commit**
- **Branches and history** — create and delete branches, list commits, inspect a commit with its diff
- **Deleting is gated twice** — `delete_repo` is off by default and additionally requires the repository name to be repeated verbatim in the call
- **New repositories are private by default** — public only when explicitly asked for
- **Runs locally** — the server talks to `api.github.com` directly; nothing is sent to a third party
- **No credentials in the package** — Claude Desktop stores the token in the OS keychain
- **Tiny package** — uv fetches Python and the dependencies on first start, so the `.mcpb` stays a few KB

## Requirements

- Claude Desktop 0.10.0 or newer (Windows, macOS, Linux)
- [uv](https://docs.astral.sh/uv/) on the target machine — it fetches Python and the dependencies (`mcp`, `httpx`) on first start
- A GitHub personal access token (see below)

```powershell
winget install --id=astral-sh.uv -e
```

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart Claude Desktop afterwards, otherwise it will not see the new `PATH` and the extension stops at "server disconnected". Check with `uv --version`.

### Token

GitHub → Settings → Developer settings → Personal access tokens.

**Classic token** (the simpler route):

| Scope | What for |
|---|---|
| `repo` | read and write everything in repositories, private ones included — mandatory |
| `workflow` | only needed to write files under `.github/workflows/` |
| `delete_repo` | only needed to delete whole repositories |

**Fine-grained token** (tighter, but maintained per repository or organisation):

| Permission | Level |
|---|---|
| Metadata | Read (GitHub requires it anyway) |
| Contents | Read and write |
| Administration | Read and write — only for `create_repo`, `update_repo`, `delete_repo` |

Without `Administration`, reading and writing code keeps working; only creating and changing repositories fails with a 403. Fine-grained tokens expire — once they do, every request returns a 401, and the fix is a new token pasted into the settings.

## Installation

1. Build `github-1.0.0.mcpb` (see [Building the .mcpb yourself](#building-the-mcpb-yourself)) and copy it to the target machine.
2. Claude Desktop → **Settings → Extensions → Advanced settings → Install extension…**, pick the file. Drag and drop onto the extensions window works too.
3. Fill in the fields (see next section) and enable the extension. The first start takes a moment while uv resolves dependencies.
4. Ask Claude something like *"which repos do I have?"*.

If something misbehaves, `check_connection` is the first thing to reach for: it reports who the token belongs to, which scopes it carries and how much API quota is left.

## Configuration

| Field | Meaning |
|---|---|
| **GitHub-Token** | The personal access token from above. Stored in the OS keychain, never in the package. |
| **Standard-Konto oder Organisation** | Optional. With `my-company` entered here, "the repo `webshop`" means `my-company/webshop`. Leave empty for the account the token belongs to. A spelled-out `owner/name` always beats this default. |
| **Neue Repositories privat anlegen** | Leave **on** unless public repositories are your normal case. |
| **Repositories löschen erlauben** | Leave **off**. Turn it on only when deletion is genuinely needed. |

The labels are German because the extension manifest is; the fields behave exactly as described above.

## Tools

| Tool | Purpose |
|---|---|
| `check_connection` | Verify the token: account, scopes, remaining API quota |
| `list_repos` | Repositories of an account or organisation |
| `get_repo` | One repository with its key figures |
| `search_repos` | Search repositories on GitHub |
| `create_repo` | New repository, optionally with README, `.gitignore` and licence |
| `update_repo` | Change description, homepage, topics, visibility, name or default branch |
| `delete_repo` | Delete a repository permanently (only if allowed in the settings) |
| `list_files` | Files and folders of a repository |
| `read_file` | Read one file |
| `get_readme` | Read the README |
| `search_code` | Search code, optionally limited to one repository |
| `write_file` | Create or overwrite a file and commit |
| `delete_file` | Delete a file and commit |
| `push_files` | Write, change and delete several files in a single commit |
| `list_branches` | List branches |
| `create_branch` | Create a branch |
| `delete_branch` | Delete a branch |
| `list_commits` | Commit history, optionally for one file |
| `get_commit` | One commit with changed files and diff |

`push_files` is the right tool for more than one file: one commit for everything, instead of one per file. If the given branch does not exist yet it is created along the way — and in a freshly created, still empty repository the same call writes the very first commit.

## How it works

1. Claude Desktop starts `server/server.py` through `uv run --script`; the dependencies live in the PEP 723 header of that one file.
2. The configured fields arrive as environment variables; the token is read from the OS keychain by Claude Desktop, not from the package.
3. Every call goes to the GitHub REST API over HTTPS. Responses are reduced to the fields that matter before they reach the model, and file contents above the inline limit come back as a size rather than as base64.
4. Repository names without an `owner/` prefix are completed with the configured default account.

## Environment variables

Useful if you want to run the server standalone rather than as an extension:

| Variable | Meaning |
|---|---|
| `GITHUB_TOKEN` | Personal access token, classic or fine-grained |
| `GITHUB_DEFAULT_OWNER` | Account or organisation assumed when a repository is named without `owner/` |
| `GITHUB_PRIVATE_REPOS` | `true` (default) → new repositories are private |
| `GITHUB_ALLOW_DELETE` | `false` (default) → `delete_repo` is refused |
| `GITHUB_API_URL` | API root, default `https://api.github.com`. Not offered in the settings dialog; here for GitHub Enterprise. |
| `GITHUB_TIMEOUT` | Seconds per HTTP request, default `30` |

```bash
uv run --script server/server.py
```

## What it does not do

- **No merges, no pull requests, no issues, no workflow runs.**
- **No `git` client.** Nothing is checked out locally, everything goes through the REST API — searching a large repository exhaustively is expensive accordingly.
- **No way around branch protection.** If the default branch is protected, direct writes fail; work on a branch of your own instead.

## Notes & caveats

- **The extension has full write access** to everything the token can see. Anything written lands in the real repository immediately — there is no staging and no undo.
- **Two brakes are built in:** deleting repositories is off from the factory and must be enabled in the settings, and the repository name has to be confirmed verbatim in the call. New repositories are private by default.
- **Keep the blast radius small** by giving a fine-grained token only the repositories Claude is actually meant to work on.
- **The `.mcpb` contains no token.** It is asked for at install time and kept in the OS keychain, so the file can be passed around without a second thought.
- **The German field labels are not a bug**, just the language the manifest was written in.

## Troubleshooting

**"Server disconnected", log shows `ModuleNotFoundError: No module named '_win32sysloader'`**

The uv environment was built incompletely. On Windows, `mcp` pulls in `pywin32`; if Claude Desktop starts the server several times at once on the very first run, the parallel uv processes can collide while creating the same cache environment — the `win32` folder then ends up without any `.pyd` files at all. This only affects the cold start of a new environment, i.e. after installation or a version change.

Throw the environment away and let it rebuild once by hand:

```powershell
Get-ChildItem "$env:LOCALAPPDATA\uv\cache\environments-v2" -Directory | Where-Object { -not (Test-Path "$($_.FullName)\Lib\site-packages\win32\_win32sysloader.pyd") -and (Test-Path "$($_.FullName)\Lib\site-packages\win32") } | Remove-Item -Recurse -Force
```

```powershell
uv run --script "$env:APPDATA\Claude\Claude Extensions\local.mcpb.thomas-weirich.github\server\server.py"
```

The second command rebuilds the environment and then sits there silently — that is the server waiting on stdin, end it with Ctrl+C. Afterwards disable and re-enable the extension in Claude Desktop.

**"Bad credentials" / 401** — token expired or pasted wrong. Fine-grained tokens have an expiry date.

**403 when creating or changing a repository** — the fine-grained token is missing `Administration: Read and write`. Reading and writing code is unaffected.

**403 when writing under `.github/workflows/`** — the classic token is missing the `workflow` scope.

## Building the .mcpb yourself

```bash
npx @anthropic-ai/mcpb pack . github-1.0.0.mcpb
```

There is nothing to compile — `server/server.py` carries its dependencies in a PEP 723 header and uv resolves them at first start. For a new version, bump `version` in `manifest.json` and carry the file name along.

## Repository layout

```
manifest.json     metadata, user_config, start command
server/server.py  the server; dependencies as a PEP 723 header in the file
icon.png
```

## Support this project ❤️

If this extension saved you time, you can support further development:

[![Donate with PayPal](https://www.paypalobjects.com/en_US/i/btn/btn_donate_LG.gif)](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)

**[➡️ Donate via PayPal](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)**

## License

This project is licensed under the [MIT License](LICENSE) — © 2026 Thomas Weirich.
