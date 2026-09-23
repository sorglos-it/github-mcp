#!/usr/bin/env python3
"""Check the server the way Claude does: over MCP on stdin/stdout.

    python apps/server/tests/test_server.py

Two parts. The offline part always runs and needs no token: it drives the tools
with input that must be refused and reads the refusal, because a tool failure
only reaches Claude when it is raised as a ToolError.

    python apps/server/tests/test_server.py --live

adds the live part, which reads from api.github.com - never a write, so a token
with wide permissions is not put at risk. The token comes from GITHUB_TOKEN or,
failing that, from the GitHub CLI; nobody has to paste it and it is never printed.

The live part reads public repositories that may move. A target that has gone
is reported as SKIP, not as a failure - nothing is wrong with the server then.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server.py"
# A path with a space and a folder with a space, in a repository that has
# carried them for years. Only ever read.
SPACED_REPO = "dotnet/samples"
SPACED_DIR = "framework/wcf/Basic/Ajax/ComplexTypeAjaxService/VB/service/My Project"
SPACED_FILE = SPACED_DIR + "/AssemblyInfo.vb"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def record(state: str, label: str, detail: str) -> None:
    results.append((state, label, detail))
    print(f"[{state}] {label}: {detail}")


class Server:
    """One server process, spoken to over MCP on stdin/stdout."""

    def __init__(self, **env_overrides: str):
        env = dict(os.environ)
        env.setdefault("GITHUB_TOKEN", "ghp_nonsense_never_used_offline")
        env.pop("GITHUB_DEFAULT_OWNER", None)
        env["GITHUB_ALLOW_DELETE"] = "false"
        env.update(env_overrides)
        self.p = subprocess.Popen(
            ["uv", "run", "--script", str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
        self.log: list[str] = []
        threading.Thread(target=self._drain, daemon=True).start()
        self._id = 0
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"}}})
        self.info = self._read(1)["result"]["serverInfo"]
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _drain(self) -> None:
        for line in self.p.stderr:
            self.log.append(line)

    def _send(self, message: dict) -> None:
        self.p.stdin.write(json.dumps(message) + "\n")
        self.p.stdin.flush()

    def _read(self, want: int) -> dict:
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise SystemExit("the server stopped:\n" + "".join(self.log[-20:]))
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == want:
                return message

    def call(self, tool: str, **arguments) -> tuple[dict | None, str]:
        """Returns (parsed result, text). The result is None on a tool error."""
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments}})
        result = self._read(self._id).get("result", {})
        text = " ".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            return None, text
        try:
            return json.loads(text), text
        except ValueError:
            return None, text

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=30)


def expect_refusal(gh: Server, label: str, tool: str, wanted: str, **arguments) -> None:
    """The tool must refuse, and say why. A 401 means it asked GitHub instead of
    refusing - the check belongs before the first request, not after it."""
    data, text = gh.call(tool, **arguments)
    if data is not None:
        record(FAIL, label, f"went through: {text[:90]}")
    elif "GitHub 401" in text or "Cannot reach" in text:
        record(FAIL, label, f"reached the network first: {text[:90]}")
    elif wanted not in text:
        record(FAIL, label, f"refused, but not for this reason: {text[:90]}")
    else:
        record(PASS, label, text[:95])


def offline() -> None:
    """No token, no network. Every refusal has to carry its reason."""
    gh = Server()
    print(f"\n--- offline, server {gh.info.get('name')} {gh.info.get('version')} ---")

    # The reason has to reach Claude at all: anything but a ToolError arrives
    # as a bare "Error executing tool <name>".
    expect_refusal(gh, "empty repository named", "get_repo", "No repository given.", repo="")
    expect_refusal(gh, "no fields to update", "update_repo",
                   "No fields given to update.", repo="o/n")
    expect_refusal(gh, "no files to push", "push_files",
                   "No files given.", repo="o/n", message="m", files=[])
    expect_refusal(gh, "deletion switched off", "delete_repo",
                   "switched off", repo="o/n", confirm="o/n")

    # Nothing may leave the address it belongs to: httpx drops ".." segments on
    # the way out, so "o/n/../../../user" would land on a different endpoint.
    expect_refusal(gh, "'..' in the repository", "get_repo",
                   "as owner/name", repo="a/b/../../../user")
    expect_refusal(gh, "'..' in a path to read", "read_file",
                   "'.' or '..'", repo="o/n", path="../../../../user")
    expect_refusal(gh, "'..' in a path to list", "list_files",
                   "'.' or '..'", repo="o/n", path="a/../../../user")
    expect_refusal(gh, "'..' in a path to write", "write_file",
                   "'.' or '..'", repo="o/n", path="../../../x", content="z")
    expect_refusal(gh, "'..' in a path to delete", "delete_file",
                   "'.' or '..'", repo="o/n", path="../../../x")
    expect_refusal(gh, "'..' in a pushed path", "push_files", "'.' or '..'",
                   repo="o/n", message="m", files=[{"path": "../../evil", "content": "x"}])
    expect_refusal(gh, "'..' deleting another branch", "delete_branch",
                   "not a valid git name", repo="o/n",
                   branch="x/../../../../repos/victim/repo/git/refs/heads/main")
    expect_refusal(gh, "'..' in a new branch name", "create_branch",
                   "not a valid git name", repo="o/n", branch="x/../../../y")
    expect_refusal(gh, "'..' in a commit", "get_commit",
                   "not a valid git name", repo="o/n", sha="../../../../user")

    # "?" and "#" end the path early, so they must not survive unescaped.
    expect_refusal(gh, "'?' in the repository", "get_repo", "as owner/name", repo="o/n?x=1")
    expect_refusal(gh, "'#' in the repository", "get_repo", "as owner/name", repo="o/n#frag")
    expect_refusal(gh, "path in an account name", "list_repos",
                   "is not a GitHub name", owner="o/../../x")
    expect_refusal(gh, "extra qualifier in an account name", "search_code",
                   "is not a GitHub name", query="q", owner="o stars:>1")
    expect_refusal(gh, "path in an organisation", "create_repo",
                   "is not a GitHub name", name="x", org="o/../y")
    gh.close()

    # The confirmation is only reachable once the switch allows deleting at all -
    # with it off, the switch refuses first, and nothing else is ever asked.
    allowed = Server(GITHUB_ALLOW_DELETE="true")
    expect_refusal(allowed, "confirmation does not match", "delete_repo",
                   "Confirmation does not match", repo="o/n", confirm="nope")
    allowed.close()


def live(token: str) -> None:
    """Reads from api.github.com. No call here writes anything."""
    gh = Server(GITHUB_TOKEN=token)
    print(f"\n--- live against api.github.com, server {gh.info.get('version')} ---")

    data, text = gh.call("check_connection")
    if not (data and data.get("login")):
        record(FAIL, "token works", text[:120])
        gh.close()
        return
    record(PASS, "token works", f"{data['login']}, {data.get('rate_limit_remaining')} calls left")

    # The point of the escaping: a space has to arrive as %20, not a broken URL.
    data, text = gh.call("read_file", repo=SPACED_REPO, path=SPACED_FILE)
    if data and data.get("content"):
        record(PASS, "read a path with a space", f"{data.get('bytes')} bytes")
    elif "404" in text:
        record(SKIP, "read a path with a space", "target moved: " + SPACED_FILE)
    else:
        record(FAIL, "read a path with a space", text[:120])

    data, text = gh.call("list_files", repo=SPACED_REPO, path=SPACED_DIR)
    if data and data.get("count"):
        record(PASS, "list a folder with a space", f"{data['count']} entries")
    elif "404" in text:
        record(SKIP, "list a folder with a space", "target moved: " + SPACED_DIR)
    else:
        record(FAIL, "list a folder with a space", text[:120])

    # A slash belongs to many branch names and has to stay a separator.
    data, _ = gh.call("list_branches", repo=SPACED_REPO, limit=100)
    slashed = next((b["name"] for b in (data or {}).get("branches", []) if "/" in b["name"]), None)
    if not slashed:
        record(SKIP, "ref with a slash", f"no such branch in {SPACED_REPO} right now")
    else:
        data, text = gh.call("list_files", repo=SPACED_REPO, path="", ref=slashed)
        record(PASS if data and data.get("count") else FAIL, "ref with a slash",
               f"{slashed!r}: {data['count']} entries" if data else text[:120])

    # Ordinary input must keep working.
    data, text = gh.call("get_repo", repo="https://github.com/octocat/Hello-World.git")
    record(PASS if data and data.get("full_name") == "octocat/Hello-World" else FAIL,
           "a pasted github.com URL", data.get("full_name") if data else text[:120])

    data, text = gh.call("list_commits", repo="sorglos-it/github-mcp", limit=3)
    sha = (data or {}).get("commits", [{}])[0].get("sha")
    record(PASS if sha else FAIL, "list commits", f"newest {sha[:8]}" if sha else text[:120])
    if sha:
        data, text = gh.call("get_commit", repo="sorglos-it/github-mcp", sha=sha)
        record(PASS if data and data.get("files") else FAIL, "read that commit",
               f"{len(data['files'])} files" if data else text[:120])

    # And the refusals still hold with a real token in hand.
    expect_refusal(gh, "still refused with a token", "read_file",
                   "'.' or '..'", repo=SPACED_REPO, path="../../../../user")
    gh.close()


def github_token() -> str:
    """The token for the live part: GITHUB_TOKEN if it is set, otherwise the one
    the GitHub CLI keeps. It goes straight into the server process and is never
    printed - which is why this asks gh instead of asking anyone to paste it."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    if not shutil.which("gh"):
        sys.exit("test: --live needs a token. Either set GITHUB_TOKEN, or install the\n"
                 "      GitHub CLI (https://cli.github.com) and run: gh auth login")
    done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    token = done.stdout.strip()
    if done.returncode != 0 or not token:
        sys.exit("test: the GitHub CLI holds no token - run: gh auth login")
    return token


def main() -> int:
    if not SERVER.is_file():
        sys.exit(f"test: no server at {SERVER}")
    if not shutil.which("uv"):
        sys.exit("test: uv is missing - it starts the server (https://docs.astral.sh/uv)")

    offline()
    if "--live" in sys.argv[1:] or os.environ.get("GITHUB_TOKEN", "").strip():
        live(github_token())
    else:
        print("\n--- live part skipped. To read from api.github.com as well:\n"
              "    python apps/server/tests/test_server.py --live")

    failed = [r for r in results if r[0] == FAIL]
    skipped = [r for r in results if r[0] == SKIP]
    print(f"\n{len(results) - len(failed) - len(skipped)}/{len(results)} passed"
          + (f", {len(skipped)} skipped" if skipped else ""))
    for state, label, detail in failed:
        print(f"  !! {label}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
