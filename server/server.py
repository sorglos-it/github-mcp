# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0,<3", "httpx>=0.27"]
# ///
"""
GitHub MCP server, focused on repositories and their contents.

Covers the repo lifecycle end to end: create, describe, browse, read and write
code, commit, delete. Every response is trimmed on the way out - a single
/repos object carries well over a hundred fields, of which a handful matter,
and the rest would only crowd out the model's context.

Config via environment:
  GITHUB_TOKEN          personal access token, classic or fine-grained
  GITHUB_DEFAULT_OWNER  user or organisation assumed when a repository is named
                        without an "owner/" prefix (default: the token's account)
  GITHUB_ALLOW_DELETE   "true"/"false" (default false) - gates delete_repo
  GITHUB_PRIVATE_REPOS  "true"/"false" (default true) - visibility of new repos
                        when create_repo is called without an explicit choice

  GITHUB_API_URL        API root (default https://api.github.com). Not offered
                        in the settings dialog; here for GitHub Enterprise.
  GITHUB_TIMEOUT        seconds allowed per HTTP request (default 30)
"""

from __future__ import annotations

import base64
import datetime as dt
import os
from typing import Any

import httpx
from pydantic import BaseModel, Field
from mcp.server import MCPServer

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def _flag(name: str, default: bool = True) -> bool:
    """Read a boolean env var. Blank counts as unset: Claude Desktop injects an
    empty string for a user_config switch it has no value for, and silently
    reading that as "off" would flip the behaviour behind the user's back."""
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off")


def _seconds(name: str, default: float) -> float:
    """Read a timeout in seconds. Blank, unparsable or non-positive falls back to
    the default rather than raising: a typo in this field must not be the reason
    the whole server refuses to start."""
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        secs = float(v)
    except ValueError:
        return default
    return secs if secs > 0 else default


TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
DEFAULT_OWNER = os.environ.get("GITHUB_DEFAULT_OWNER", "").strip()
ALLOW_DELETE = _flag("GITHUB_ALLOW_DELETE", False)
PRIVATE_BY_DEFAULT = _flag("GITHUB_PRIVATE_REPOS", True)
API_URL = (os.environ.get("GITHUB_API_URL", "").strip() or "https://api.github.com").rstrip("/")
TIMEOUT = _seconds("GITHUB_TIMEOUT", 30.0)

API_VERSION = "2022-11-28"

# The contents API returns base64 only below 1 MB; above that it answers with an
# empty body and expects the raw media type instead.
JSON_CONTENT_LIMIT = 1_000_000
# Ceiling for how much file text a single read hands back by default.
MAX_INLINE_BYTES = 100_000

mcp = MCPServer("github", version="1.0.0")


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

def _clean(d: dict) -> dict:
    """Drop keys that are None. Shallow on purpose: nested tree entries use an
    explicit null sha to mean "delete this path", which must survive."""
    return {k: v for k, v in d.items() if v is not None}


class GitHub:
    def __init__(self) -> None:
        if not TOKEN:
            raise GitHubError(
                "No token configured. Open the extension settings and paste a "
                "GitHub token (Settings -> Developer settings -> Personal access tokens)."
            )
        self._c = httpx.Client(
            base_url=API_URL,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "github-mcpb",
            },
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        self._login: str | None = None
        self._scopes: str | None = None

    # -- plumbing ----------------------------------------------------------

    @staticmethod
    def _explain(r: httpx.Response) -> str:
        """Turn a GitHub error response into one sentence a human can act on."""
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        msg = data.get("message") or r.reason_phrase or "request failed"

        if r.status_code == 401:
            msg += " - the token is missing, invalid or expired."
        elif r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
            reset = r.headers.get("x-ratelimit-reset", "")
            when = ""
            if reset.isdigit():
                when = dt.datetime.fromtimestamp(int(reset)).strftime(" (until %H:%M)")
            msg = f"GitHub rate limit reached{when}."
        elif r.status_code == 403:
            msg += (" - the token may not carry the permission this call needs "
                    "(classic: 'repo'; fine-grained: the matching repository permission).")
        elif r.status_code == 404:
            msg += (" - not found, or invisible to this token. Private repositories "
                    "need the 'repo' scope, fine-grained tokens need the repository "
                    "in their access list.")
        elif r.status_code == 409:
            msg += " - conflict; the branch moved or the repository is empty."

        detail = []
        for e in data.get("errors") or []:
            if isinstance(e, dict):
                bits = [str(e[k]) for k in ("resource", "field", "code", "message") if e.get(k)]
                detail.append(" ".join(bits))
            else:
                detail.append(str(e))
        out = f"GitHub {r.status_code}: {msg}"
        if detail:
            out += " (" + "; ".join(detail) + ")"
        return out

    def request(self, method: str, path: str, *, params: dict | None = None,
                json: Any = None, accept: str | None = None) -> httpx.Response:
        headers = {"Accept": accept} if accept else None
        try:
            r = self._c.request(method, path, params=_clean(params or {}) or None,
                                json=json, headers=headers)
        except httpx.RequestError as e:
            raise GitHubError(f"Cannot reach {API_URL}: {e}") from e
        if "x-oauth-scopes" in r.headers:
            self._scopes = r.headers.get("x-oauth-scopes", "")
        if r.status_code >= 400:
            raise GitHubError(self._explain(r), r.status_code)
        return r

    def json(self, method: str, path: str, **kw) -> Any:
        r = self.request(method, path, **kw)
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()

    def maybe(self, method: str, path: str, **kw) -> Any:
        """Like json(), but a 404 comes back as None instead of raising."""
        try:
            return self.json(method, path, **kw)
        except GitHubError as e:
            if e.status == 404:
                return None
            raise

    def paged(self, path: str, *, params: dict | None = None, limit: int = 50) -> list[dict]:
        """Follow pages until limit is reached. Caps at 10 pages so a wide query
        cannot turn into a hundred silent round trips."""
        limit = max(1, min(limit, 1000))
        per_page = min(100, limit)
        out: list[dict] = []
        p = dict(params or {})
        p["per_page"] = per_page
        for page in range(1, 11):
            p["page"] = page
            data = self.json("GET", path, params=p)
            items = data.get("items") if isinstance(data, dict) else data
            if not items:
                break
            out.extend(items)
            if len(items) < per_page or len(out) >= limit:
                break
        return out[:limit]

    # -- identity ----------------------------------------------------------

    def me(self) -> str:
        if self._login is None:
            self._login = self.json("GET", "/user").get("login", "")
        return self._login

    @property
    def scopes(self) -> str | None:
        return self._scopes


_client: GitHub | None = None


def client() -> GitHub:
    global _client
    if _client is None:
        _client = GitHub()
    return _client


# --------------------------------------------------------------------------
# naming and trimming
# --------------------------------------------------------------------------

def repo_path(repo: str) -> str:
    """Normalise a repository reference to "owner/name".

    Accepts "owner/name", a bare "name" (resolved against the configured default
    owner, otherwise the token's own account) and a pasted github.com URL.
    """
    r = (repo or "").strip().strip("/")
    if not r:
        raise GitHubError("No repository given.")
    if "github.com" in r:
        r = r.split("github.com", 1)[1].lstrip(":/")
        r = "/".join(r.split("/")[:2])
    if r.endswith(".git"):
        r = r[:-4]
    if "/" in r:
        owner, _, name = r.partition("/")
    else:
        owner, name = DEFAULT_OWNER or client().me(), r
    owner, name = owner.strip(), name.strip()
    if not owner or not name:
        raise GitHubError(f"Cannot read '{repo}' as owner/name.")
    return f"{owner}/{name}"


def trim_repo(d: dict) -> dict:
    return _clean({
        "full_name": d.get("full_name"),
        "private": d.get("private"),
        "description": d.get("description"),
        "default_branch": d.get("default_branch"),
        "url": d.get("html_url"),
        "clone_url": d.get("clone_url"),
        "homepage": d.get("homepage") or None,
        "language": d.get("language"),
        "topics": d.get("topics") or None,
        "stars": d.get("stargazers_count"),
        "forks": d.get("forks_count"),
        "open_issues": d.get("open_issues_count"),
        "size_kb": d.get("size"),
        "is_fork": d.get("fork") or None,
        "archived": d.get("archived") or None,
        "pushed_at": d.get("pushed_at"),
    })


def trim_commit(d: dict) -> dict:
    c = d.get("commit") or {}
    author = c.get("author") or {}
    return _clean({
        "sha": d.get("sha"),
        "message": (c.get("message") or "").split("\n")[0],
        "author": author.get("name"),
        "date": author.get("date"),
        "url": d.get("html_url"),
    })


def _decode(content: str, encoding: str | None) -> bytes:
    if encoding == "base64":
        return base64.b64decode(content)
    return (content or "").encode("utf-8")


def _as_text(data: bytes, max_bytes: int) -> dict:
    """Text if it decodes as UTF-8, otherwise a description of the blob. Binary
    payloads are never inlined: a PNG in base64 is pure context noise."""
    if b"\0" in data[:8000]:
        return {"binary": True, "bytes": len(data)}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"binary": True, "bytes": len(data)}
    if max_bytes and len(data) > max_bytes:
        return {"content": data[:max_bytes].decode("utf-8", "ignore"),
                "truncated": True, "bytes": len(data),
                "note": f"Only the first {max_bytes} bytes are shown. "
                        f"Raise max_bytes for more."}
    return {"content": text, "bytes": len(data)}


def _ref_sha(gh: GitHub, rp: str, branch: str) -> str | None:
    d = gh.maybe("GET", f"/repos/{rp}/git/ref/heads/{branch}")
    return (d or {}).get("object", {}).get("sha")


def _resolve_sha(gh: GitHub, rp: str, ref: str) -> str:
    """Resolve a branch name, tag name or commit sha to a commit sha."""
    for p in (f"/repos/{rp}/git/ref/heads/{ref}", f"/repos/{rp}/git/ref/tags/{ref}"):
        d = gh.maybe("GET", p)
        if d:
            return d["object"]["sha"]
    d = gh.maybe("GET", f"/repos/{rp}/commits/{ref}")
    if d:
        return d["sha"]
    raise GitHubError(f"Cannot resolve '{ref}' in {rp} - no such branch, tag or commit.")


def _default_branch(gh: GitHub, rp: str) -> str:
    return gh.json("GET", f"/repos/{rp}").get("default_branch") or "main"


# --------------------------------------------------------------------------
# MCP tools: connection
# --------------------------------------------------------------------------

@mcp.tool()
def check_connection() -> dict:
    """Verify the token and report who it belongs to, what it may do and how
    much API budget is left. First thing to run when something fails."""
    gh = client()
    user = gh.json("GET", "/user")
    limits = (gh.json("GET", "/rate_limit").get("resources") or {}).get("core") or {}
    reset = limits.get("reset")
    scopes = gh.scopes
    return _clean({
        "login": user.get("login"),
        "name": user.get("name"),
        "account_type": user.get("type"),
        "token_scopes": scopes if scopes else "not reported (fine-grained token)",
        "default_owner": DEFAULT_OWNER or user.get("login"),
        "repo_deletion_enabled": ALLOW_DELETE,
        "new_repos_private_by_default": PRIVATE_BY_DEFAULT,
        "api": API_URL,
        "rate_limit_remaining": limits.get("remaining"),
        "rate_limit_reset": dt.datetime.fromtimestamp(reset).strftime("%H:%M") if reset else None,
    })


# --------------------------------------------------------------------------
# MCP tools: repositories
# --------------------------------------------------------------------------

@mcp.tool()
def list_repos(owner: str | None = None, kind: str = "all", sort: str = "updated",
               include_forks: bool = True, limit: int = 50) -> dict:
    """List repositories.

    owner: user or organisation. Omitted lists everything the token can see,
    including private repos and org membership.
    kind: all, owner, member, public, private (applies to the token's own list).
    sort: updated, created, pushed, full_name.
    """
    gh = client()
    if owner:
        is_org = gh.maybe("GET", f"/orgs/{owner}") is not None
        path = f"/orgs/{owner}/repos" if is_org else f"/users/{owner}/repos"
        params = {"sort": sort, "type": kind if kind in ("all", "public", "private",
                                                         "forks", "sources", "member") else "all"}
    else:
        path = "/user/repos"
        params = {"sort": sort, "affiliation": "owner,collaborator,organization_member"}
        if kind in ("owner", "member", "public", "private"):
            params = {"sort": sort, "type": kind}
    rows = gh.paged(path, params=params, limit=limit if include_forks else limit * 2)
    if not include_forks:
        rows = [r for r in rows if not r.get("fork")][:limit]
    return {"owner": owner or gh.me(), "count": len(rows),
            "repos": [trim_repo(r) for r in rows]}


@mcp.tool()
def get_repo(repo: str) -> dict:
    """Full detail for one repository, including default branch and topics."""
    gh = client()
    rp = repo_path(repo)
    d = gh.json("GET", f"/repos/{rp}")
    out = trim_repo(d)
    out["license"] = (d.get("license") or {}).get("spdx_id")
    out["has_issues"] = d.get("has_issues")
    out["has_wiki"] = d.get("has_wiki")
    out["visibility"] = d.get("visibility")
    out["created_at"] = d.get("created_at")
    return _clean(out)


@mcp.tool()
def search_repos(query: str, limit: int = 20, sort: str | None = None) -> dict:
    """Search repositories across GitHub.

    Qualifiers work as on the website: "user:octocat language:python",
    "org:anthropics stars:>100". sort: stars, forks, updated.
    """
    gh = client()
    data = gh.json("GET", "/search/repositories",
                   params={"q": query, "sort": sort, "order": "desc",
                           "per_page": max(1, min(limit, 100))})
    items = data.get("items") or []
    return {"query": query, "total": data.get("total_count"),
            "repos": [trim_repo(r) for r in items[:limit]]}


@mcp.tool()
def create_repo(name: str, description: str | None = None, private: bool | None = None,
                org: str | None = None, auto_init: bool = True,
                gitignore_template: str | None = None, license_template: str | None = None,
                homepage: str | None = None, topics: list[str] | None = None) -> dict:
    """Create a repository.

    private: defaults to the extension setting (private unless changed).
    org: create it under an organisation instead of the personal account.
    auto_init: lay down an initial commit with a README, so files can be written
    right away. Without it the repository starts empty.
    gitignore_template: e.g. "Python", "Node". license_template: e.g. "mit", "apache-2.0".
    """
    gh = client()
    body = _clean({
        "name": name,
        "description": description,
        "homepage": homepage,
        "private": PRIVATE_BY_DEFAULT if private is None else private,
        "auto_init": auto_init,
        "gitignore_template": gitignore_template,
        "license_template": license_template,
    })
    d = gh.json("POST", f"/orgs/{org}/repos" if org else "/user/repos", json=body)
    if topics:
        gh.json("PUT", f"/repos/{d['full_name']}/topics", json={"names": topics})
        d["topics"] = topics
    return {"created": True, "repo": trim_repo(d)}


@mcp.tool()
def update_repo(repo: str, name: str | None = None, description: str | None = None,
                homepage: str | None = None, private: bool | None = None,
                default_branch: str | None = None, topics: list[str] | None = None,
                archived: bool | None = None, has_issues: bool | None = None,
                has_wiki: bool | None = None, has_projects: bool | None = None) -> dict:
    """Change repository settings: description, homepage, visibility, topics,
    default branch, or rename it via name. Only the fields given are touched.

    Passing topics replaces the whole list. Passing archived=true freezes the
    repository read-only; only the web UI can undo that.
    """
    gh = client()
    rp = repo_path(repo)
    body = _clean({
        "name": name, "description": description, "homepage": homepage,
        "private": private, "default_branch": default_branch, "archived": archived,
        "has_issues": has_issues, "has_wiki": has_wiki, "has_projects": has_projects,
    })
    if not body and topics is None:
        raise GitHubError("No fields given to update.")
    d = gh.json("PATCH", f"/repos/{rp}", json=body) if body else gh.json("GET", f"/repos/{rp}")
    if topics is not None:
        gh.json("PUT", f"/repos/{d['full_name']}/topics", json={"names": topics})
        d["topics"] = topics
    return {"updated": True, "repo": trim_repo(d)}


@mcp.tool()
def delete_repo(repo: str, confirm: str) -> dict:
    """Permanently delete a repository, with everything in it. Irreversible.

    Switched off unless the extension setting allows it, and confirm must repeat
    the repository name exactly.
    """
    gh = client()
    rp = repo_path(repo)
    if not ALLOW_DELETE:
        raise GitHubError(
            "Deleting repositories is switched off. Turn on 'Repositories löschen "
            "erlauben' in the extension settings if that is really wanted.")
    want = confirm.strip().strip("/").lower()
    if want not in (rp.lower(), rp.split("/")[1].lower()):
        raise GitHubError(f"Confirmation does not match. Pass confirm=\"{rp}\" to delete it.")
    gh.json("DELETE", f"/repos/{rp}")
    return {"deleted": True, "repo": rp}


# --------------------------------------------------------------------------
# MCP tools: browsing and reading
# --------------------------------------------------------------------------

@mcp.tool()
def list_files(repo: str, path: str = "", ref: str | None = None,
               recursive: bool = False, limit: int = 300) -> dict:
    """List files and folders in a repository.

    path: subfolder, empty for the root. ref: branch, tag or commit.
    recursive: walk the whole subtree instead of one level.
    """
    gh = client()
    rp = repo_path(repo)
    path = (path or "").strip("/")

    if not recursive:
        data = gh.json("GET", f"/repos/{rp}/contents/{path}", params={"ref": ref})
        if isinstance(data, dict):
            return {"repo": rp, "path": path, "is_file": True,
                    "note": "This path is a file - use read_file."}
        rows = [{"path": e.get("path"), "type": e.get("type"), "size": e.get("size")}
                for e in data]
        rows.sort(key=lambda e: (e["type"] != "dir", e["path"].lower()))
        return {"repo": rp, "path": path or "/", "ref": ref, "count": len(rows),
                "entries": rows[:limit]}

    tree_ref = ref or _default_branch(gh, rp)
    data = gh.json("GET", f"/repos/{rp}/git/trees/{tree_ref}", params={"recursive": "1"})
    rows = []
    for e in data.get("tree") or []:
        p = e.get("path", "")
        if path and not (p == path or p.startswith(path + "/")):
            continue
        rows.append(_clean({"path": p, "type": "dir" if e.get("type") == "tree" else "file",
                            "size": e.get("size")}))
    rows.sort(key=lambda e: e["path"].lower())
    out = {"repo": rp, "path": path or "/", "ref": tree_ref, "count": len(rows),
           "entries": rows[:limit]}
    if data.get("truncated"):
        out["note"] = "GitHub truncated this tree - the repository is too large to list in one call."
    elif len(rows) > limit:
        out["note"] = f"{len(rows)} entries matched, {limit} returned. Narrow with path or raise limit."
    return out


@mcp.tool()
def read_file(repo: str, path: str, ref: str | None = None,
              max_bytes: int = MAX_INLINE_BYTES) -> dict:
    """Read one file. ref is a branch, tag or commit; omitted reads the default branch.

    Returns the text plus the blob sha needed for a conflict-free write_file.
    Binary files are reported with their size instead of their content.
    """
    gh = client()
    rp = repo_path(repo)
    path = path.strip("/")
    meta = gh.json("GET", f"/repos/{rp}/contents/{path}", params={"ref": ref})
    if isinstance(meta, list):
        raise GitHubError(f"'{path}' is a folder - use list_files.")

    size = meta.get("size") or 0
    if meta.get("content"):
        raw = _decode(meta["content"], meta.get("encoding"))
    elif size >= JSON_CONTENT_LIMIT:
        raw = gh.request("GET", f"/repos/{rp}/contents/{path}", params={"ref": ref},
                         accept="application/vnd.github.raw").content
    else:
        raw = b""
    out = {"repo": rp, "path": meta.get("path", path), "sha": meta.get("sha"),
           "ref": ref, "url": meta.get("html_url")}
    out.update(_as_text(raw, max_bytes))
    return _clean(out)


@mcp.tool()
def get_readme(repo: str, ref: str | None = None, max_bytes: int = MAX_INLINE_BYTES) -> dict:
    """Read a repository's README, whatever it is called."""
    gh = client()
    rp = repo_path(repo)
    meta = gh.maybe("GET", f"/repos/{rp}/readme", params={"ref": ref})
    if not meta:
        return {"repo": rp, "exists": False,
                "note": "No README in this repository - write_file can create one."}
    out = {"repo": rp, "path": meta.get("path"), "sha": meta.get("sha"),
           "url": meta.get("html_url")}
    out.update(_as_text(_decode(meta.get("content", ""), meta.get("encoding")), max_bytes))
    return _clean(out)


@mcp.tool()
def search_code(query: str, repo: str | None = None, owner: str | None = None,
                limit: int = 20) -> dict:
    """Search code. repo or owner narrow the search; qualifiers such as
    "language:python path:src extension:md" work as on the website.

    GitHub only indexes the default branch, and skips very large or forked
    repositories - a miss here is not proof the code is absent.
    """
    gh = client()
    q = query.strip()
    if repo:
        q += f" repo:{repo_path(repo)}"
    elif owner:
        q += f" user:{owner}"
    data = gh.json("GET", "/search/code",
                   params={"q": q, "per_page": max(1, min(limit, 100))})
    hits = [{"repo": (i.get("repository") or {}).get("full_name"),
             "path": i.get("path"), "url": i.get("html_url")}
            for i in (data.get("items") or [])[:limit]]
    return {"query": q, "total": data.get("total_count"), "matches": hits}


# --------------------------------------------------------------------------
# MCP tools: writing
# --------------------------------------------------------------------------

class FileEdit(BaseModel):
    """One path in a push_files commit. Leaving content unset deletes the path."""
    path: str = Field(description="Path inside the repository, e.g. src/main.py")
    content: str | None = Field(
        default=None,
        description="The file's new content in full. Omit or pass null to delete the path.")
    encoding: str = Field(
        default="text",
        description='"text", or "base64" when content already is base64 (binaries).')
    mode: str = Field(default="100644",
                      description='Git file mode; "100755" for an executable.')


@mcp.tool()
def write_file(repo: str, path: str, content: str, message: str | None = None,
               branch: str | None = None, sha: str | None = None,
               encoding: str = "text") -> dict:
    """Create or overwrite a single file and commit it.

    branch: omitted writes to the default branch. The existing file's sha is
    looked up automatically, so the call also works as a plain overwrite; pass
    sha explicitly to make the write fail if someone else changed the file first.
    encoding: "text", or "base64" when content already is base64 (binaries).

    For several files at once use push_files - that makes one commit instead of one per file.
    """
    gh = client()
    rp = repo_path(repo)
    path = path.strip("/")
    if sha is None:
        cur = gh.maybe("GET", f"/repos/{rp}/contents/{path}", params={"ref": branch})
        if isinstance(cur, dict):
            sha = cur.get("sha")
    payload = content if encoding == "base64" else base64.b64encode(
        content.encode("utf-8")).decode("ascii")
    body = _clean({
        "message": message or (f"Update {path}" if sha else f"Add {path}"),
        "content": payload,
        "branch": branch,
        "sha": sha,
    })
    d = gh.json("PUT", f"/repos/{rp}/contents/{path}", json=body)
    c = d.get("content") or {}
    commit = d.get("commit") or {}
    return {"written": True, "repo": rp, "path": c.get("path", path),
            "created": sha is None, "sha": c.get("sha"),
            "commit": commit.get("sha"), "url": c.get("html_url")}


@mcp.tool()
def delete_file(repo: str, path: str, message: str | None = None,
                branch: str | None = None) -> dict:
    """Delete one file and commit the deletion. The file stays in the history."""
    gh = client()
    rp = repo_path(repo)
    path = path.strip("/")
    cur = gh.json("GET", f"/repos/{rp}/contents/{path}", params={"ref": branch})
    if isinstance(cur, list):
        raise GitHubError(f"'{path}' is a folder. Delete its files, or use push_files "
                          f"to drop them all in one commit.")
    d = gh.json("DELETE", f"/repos/{rp}/contents/{path}",
                json=_clean({"message": message or f"Delete {path}",
                             "sha": cur["sha"], "branch": branch}))
    return {"deleted": True, "repo": rp, "path": path,
            "commit": (d.get("commit") or {}).get("sha")}


@mcp.tool()
def push_files(repo: str, files: list[FileEdit], message: str,
               branch: str | None = None, base_ref: str | None = None) -> dict:
    """Write, change and delete several files in a single commit.

    branch: omitted uses the default branch. If the branch does not exist yet it
    is created from base_ref (default: the default branch), which makes this the
    one-shot way to open a feature branch with content on it.
    Works on a freshly created empty repository too - then it writes the first commit.
    """
    gh = client()
    rp = repo_path(repo)
    if not files:
        raise GitHubError("No files given.")

    info = gh.json("GET", f"/repos/{rp}")
    branch = branch or info.get("default_branch") or "main"

    head = _ref_sha(gh, rp, branch)
    creating = head is None
    if creating:
        start = base_ref or info.get("default_branch")
        if start:
            try:
                head = _resolve_sha(gh, rp, start)
            except GitHubError:
                # A repository without a single commit answers 404/409 here.
                # Then this commit becomes the first one and has no parent.
                head = None

    base_tree = None
    if head:
        base_tree = (gh.json("GET", f"/repos/{rp}/git/commits/{head}").get("tree") or {}).get("sha")

    entries: list[dict] = []
    for f in files:
        p = f.path.strip("/")
        if f.content is None:
            if not base_tree:
                raise GitHubError(f"Cannot delete '{p}' - the branch has no commit yet.")
            entries.append({"path": p, "mode": f.mode, "type": "blob", "sha": None})
            continue
        blob = gh.json("POST", f"/repos/{rp}/git/blobs", json={
            "content": f.content,
            "encoding": "base64" if f.encoding == "base64" else "utf-8"})
        entries.append({"path": p, "mode": f.mode, "type": "blob", "sha": blob["sha"]})

    tree = gh.json("POST", f"/repos/{rp}/git/trees",
                   json=_clean({"base_tree": base_tree, "tree": entries}))
    commit = gh.json("POST", f"/repos/{rp}/git/commits",
                     json={"message": message, "tree": tree["sha"],
                           "parents": [head] if head else []})
    if creating:
        gh.json("POST", f"/repos/{rp}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    else:
        gh.json("PATCH", f"/repos/{rp}/git/refs/heads/{branch}", json={"sha": commit["sha"]})

    written = [f.path.strip("/") for f in files if f.content is not None]
    removed = [f.path.strip("/") for f in files if f.content is None]
    return _clean({"committed": True, "repo": rp, "branch": branch,
                   "branch_created": creating or None,
                   "commit": commit["sha"], "written": written or None,
                   "deleted": removed or None,
                   "url": f"https://github.com/{rp}/commit/{commit['sha']}"})


# --------------------------------------------------------------------------
# MCP tools: branches and history
# --------------------------------------------------------------------------

@mcp.tool()
def list_branches(repo: str, limit: int = 50) -> dict:
    """List branches, with the default branch marked."""
    gh = client()
    rp = repo_path(repo)
    default = _default_branch(gh, rp)
    rows = gh.paged(f"/repos/{rp}/branches", limit=limit)
    return {"repo": rp, "default_branch": default, "count": len(rows),
            "branches": [_clean({"name": b.get("name"),
                                 "sha": (b.get("commit") or {}).get("sha"),
                                 "protected": b.get("protected") or None,
                                 "is_default": b.get("name") == default or None})
                         for b in rows]}


@mcp.tool()
def create_branch(repo: str, branch: str, from_ref: str | None = None) -> dict:
    """Create a branch. from_ref is a branch, tag or commit; omitted branches off
    the default branch."""
    gh = client()
    rp = repo_path(repo)
    src = from_ref or _default_branch(gh, rp)
    sha = _resolve_sha(gh, rp, src)
    gh.json("POST", f"/repos/{rp}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha})
    return {"created": True, "repo": rp, "branch": branch, "from": src, "sha": sha}


@mcp.tool()
def delete_branch(repo: str, branch: str) -> dict:
    """Delete a branch. The commits survive until GitHub garbage-collects them;
    the default branch is refused."""
    gh = client()
    rp = repo_path(repo)
    if branch == _default_branch(gh, rp):
        raise GitHubError(f"'{branch}' is the default branch of {rp} and cannot be deleted.")
    gh.json("DELETE", f"/repos/{rp}/git/refs/heads/{branch}")
    return {"deleted": True, "repo": rp, "branch": branch}


@mcp.tool()
def list_commits(repo: str, branch: str | None = None, path: str | None = None,
                 author: str | None = None, since: str | None = None,
                 until: str | None = None, limit: int = 20) -> dict:
    """List commits, newest first.

    path narrows the history to one file or folder, since/until take ISO dates
    (YYYY-MM-DD or a full timestamp).
    """
    gh = client()
    rp = repo_path(repo)
    rows = gh.paged(f"/repos/{rp}/commits", limit=limit, params={
        "sha": branch, "path": path, "author": author, "since": since, "until": until})
    return {"repo": rp, "count": len(rows), "commits": [trim_commit(c) for c in rows]}


@mcp.tool()
def get_commit(repo: str, sha: str, include_patch: bool = False,
               max_patch_bytes: int = 20000) -> dict:
    """One commit with its changed files. include_patch adds the diffs, which get
    long fast - the budget in max_patch_bytes is shared across all files."""
    gh = client()
    rp = repo_path(repo)
    d = gh.json("GET", f"/repos/{rp}/commits/{sha}")
    stats = d.get("stats") or {}
    budget = max_patch_bytes
    files = []
    for f in d.get("files") or []:
        row = _clean({"path": f.get("filename"), "status": f.get("status"),
                      "additions": f.get("additions"), "deletions": f.get("deletions"),
                      "previous_path": f.get("previous_filename")})
        if include_patch and f.get("patch") and budget > 0:
            patch = f["patch"][:budget]
            budget -= len(patch)
            row["patch"] = patch
            if len(f["patch"]) > len(patch):
                row["patch_truncated"] = True
        files.append(row)
    out = trim_commit(d)
    out.update({"repo": rp, "additions": stats.get("additions"),
                "deletions": stats.get("deletions"), "files": files})
    if include_patch and budget <= 0:
        out["note"] = "Patch budget exhausted - raise max_patch_bytes or read files individually."
    return _clean(out)


if __name__ == "__main__":
    mcp.run()
