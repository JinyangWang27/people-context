# Distribution checklist

The repository side of distribution is done: `server.json`, `glama.json`, the `mcp-name:` marker in the
packaged README, the `.mcpb` attached to every release, and the Obsidian release workflow. What remains is a
set of account-owner steps that need a browser session or an OAuth device flow. This document walks through
them in the order that avoids rework: the official Registry first, because several directories consume it.

Everything below assumes a release is live on PyPI whose packaged README carries the marker
`<!-- mcp-name: io.github.JinyangWang27/people-context -->`.

> **Prerequisite, not yet satisfied.** Releases up to and including `1.1.0` shipped the lowercase
> `io.github.jinyangwang27` spelling of that marker, which the Registry rejects — the ownership check compares it
> byte-for-byte against the `name` in [`server.json`](../server.json), and the grant issued by
> `mcp-publisher login github` covers the GitHub login's own casing (`io.github.JinyangWang27/*`) only. The
> corrected marker currently exists in this repository alone; publication to the Registry stays blocked until a
> release built from it is live on PyPI. Confirm before starting step 1:
>
> ```bash
> curl -s https://pypi.org/pypi/people-context/json | grep -o 'mcp-name: [^ ]*'
> ```

Budget: about two hours end to end, most of it waiting on forms and one PR review.

## 1. Official MCP Registry

Why first: PulseMCP and mcp.so ingest the Registry, and the Registry entry is the durable public identity.

Install the pinned publisher. The digest table for every platform is in
[mcp-registry.md](mcp-registry.md#publication-manual-account-owner-step); this is the macOS arm64 row:

```bash
cd ~/code/people-context-mcp
MCP_PUBLISHER_VERSION="v1.8.0"
archive="mcp-publisher_darwin_arm64.tar.gz"
MCP_PUBLISHER_SHA256="e74f8846c3b5d0428cfeae3f9f520bbf9031d18e68224108c3760d60b6aaf2e0"
curl -fLsS -o "$archive" \
  "https://github.com/modelcontextprotocol/registry/releases/download/${MCP_PUBLISHER_VERSION}/${archive}"
actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
[ "$actual" = "$MCP_PUBLISHER_SHA256" ] || { echo "digest mismatch" >&2; exit 1; }
tar -xzf "$archive" mcp-publisher
```

Publish. `login github` opens a device-code flow in the browser; sign in as `JinyangWang27`, which is what
proves ownership of the `io.github.JinyangWang27` namespace:

```bash
./mcp-publisher login github
./mcp-publisher validate server.json
./mcp-publisher publish
```

Verify, then delete the binary and archive (they are untracked and should not be committed):

```bash
curl -s 'https://registry.modelcontextprotocol.io/v0/servers?search=people-context' | python3 -m json.tool | head -30
rm -f mcp-publisher mcp-publisher_darwin_arm64.tar.gz
```

Expected: one server named `io.github.JinyangWang27/people-context` at version `1.0.0` with a `pypi` package.

Common failures:

- `package validation failed` or a marker complaint: the Registry fetched the PyPI README and did not find the
  `mcp-name:` line. Confirm at https://pypi.org/project/people-context/ that the rendered description starts
  with it. It is an HTML comment, so view the page source.
- `namespace` or `unauthorized`: the GitHub account used in the device flow is not `JinyangWang27`.
- Version already exists: the Registry is a versioned snapshot. After the next release (`1.1.0` from PR #116),
  repeat the three commands; Release Please already bumped `server.json`.

Every future release needs the same `publish` step, since nothing automates the device flow.

## 2. Community directories

Order does not matter among these four. Each is a five-minute browser task.

### Glama

Glama already indexes public repositories that carry `glama.json`; the claim proves you are the maintainer
and unlocks the score badge used in the awesome-list entry below.

1. Open https://glama.ai/mcp/servers and search for `people-context`, or go directly to
   `https://glama.ai/mcp/servers/JinyangWang27/people-context` (Glama sometimes prefixes the owner with `@`).
2. Sign in with GitHub as `JinyangWang27`.
3. Click **Claim** on the server page. `glama.json` lists `JinyangWang27` under `maintainers`, so the claim
   should verify without a further step.
4. Note the badge URL the page offers; it is
   `https://glama.ai/mcp/servers/JinyangWang27/people-context/badges/score.svg`.

### Smithery

Smithery lists local stdio servers through an `.mcpb` bundle rather than a hosted URL.

1. Download `people-context.mcpb` from the latest release:
   https://github.com/JinyangWang27/people-context/releases/latest
2. Sign in at https://smithery.ai with GitHub, open https://smithery.ai/new, choose the local/bundle path, and
   upload the file. Name the server `JinyangWang27/people-context`.
   The CLI equivalent is `smithery mcp publish ./people-context.mcpb -n JinyangWang27/people-context`.
3. After it appears, open the server's **Settings → Verification** page and complete the GitHub ownership
   check so the listing shows as official.

Paste the README's one-line description when asked: *Local-first memory for AI agents about the people in
your life. MCP server + CLI on SQLite. Never phones home.*

### PulseMCP

PulseMCP ingests the official Registry and GitHub metadata, so do this after step 1.

1. Open https://www.pulsemcp.com/submit. The form pauses periodically; if it says submissions are closed,
   the Registry entry is enough for their crawler to pick the server up, and you can move on.
2. Submit the repository URL `https://github.com/JinyangWang27/people-context` and, if asked, the Registry
   name `io.github.JinyangWang27/people-context`.

### mcp.so

1. Open https://mcp.so/submit and sign in with GitHub.
2. Submit the repository URL. Name: `people-context`. Description: the one-liner above. Tags: `memory`,
   `local-first`, `personal-crm`, `sqlite`, `python`.

### Record it

When each listing is live, edit the matrix in [mcp-registry.md](mcp-registry.md#community-directory-matrix):
replace the *Live publication* cell's "Manual …" text with `Published YYYY-MM-DD` and the listing URL.
That doc has no test pinning those cells, so it is a plain edit. Commit it as
`docs: record directory publication dates`.

## 3. Awesome lists

### punkpeye/awesome-mcp-servers

This is the list that matters (93k stars). Entries sit under **🧠 Knowledge & Memory**, one per line, in
alphabetical order by repository path, and every recent entry follows one shape: link, Glama score badge,
language and platform icons, a description, and an install command in backticks. Legend: 🐍 Python,
🏠 local service, 🍎 🪟 🐧 the platforms it runs on.

```bash
gh repo fork punkpeye/awesome-mcp-servers --clone
cd awesome-mcp-servers
git checkout -b add-people-context
```

Find the `<a name="knowledge--memory">` heading in `README.md` and insert this line at its alphabetical
position (entries are compared by the `owner/repo` text, so it goes among the `J`s):

```markdown
- [JinyangWang27/people-context](https://github.com/JinyangWang27/people-context) [![JinyangWang27/people-context MCP server](https://glama.ai/mcp/servers/JinyangWang27/people-context/badges/score.svg)](https://glama.ai/mcp/servers/JinyangWang27/people-context) 🐍 🏠 🍎 🪟 🐧 - Local-first memory for AI agents about the people in your life: explainable name resolution, relationships, roles, facts, interactions, and communication guidance in one SQLite file you own. Imports are staged for review, sensitive records sit behind an operator gate, and nothing leaves the machine. `uvx --from people-context people-context`
```

Then:

```bash
git commit -am "Add people-context to Knowledge & Memory"
git push -u origin add-people-context
gh pr create --title "Add people-context (Knowledge & Memory)" \
  --body "Local-first MCP server + CLI giving agents durable context about the people in the user's life. Python, stdio, SQLite, no network. Repo: https://github.com/JinyangWang27/people-context — PyPI: https://pypi.org/project/people-context/ — Registry: io.github.JinyangWang27/people-context"
```

Do the Glama claim first so the badge in the line renders a real score instead of "unknown".

### wong2/awesome-mcp-servers

Does not accept pull requests. Submit through the form at https://mcpservers.org/submit with the repository
URL and the one-line description.

### appcypher/awesome-mcp-servers

Accepts pull requests. Its closest section is **📝 Note Taking** (personal knowledge management); if a
**Knowledge & Memory** section exists when you look, prefer it. Entry shape, alphabetical within the section:

```markdown
- [people-context](https://github.com/JinyangWang27/people-context) - Local-first memory for AI agents about the people in your life: identity resolution, relationships, facts, interactions, and communication guidance in a user-owned SQLite file
```

Fork, branch, one-line commit, PR, exactly as above.

## 4. Claude Desktop extension directory

Anthropic curates the directory through an interest form rather than a registry. The bundle is already built
and attached to every release by `release.yml`.

1. Have these ready:
   - bundle URL: `https://github.com/JinyangWang27/people-context/releases/latest/download/people-context.mcpb`
   - name: People Context; one-line description as above
   - privacy policy URL: `https://github.com/JinyangWang27/people-context/blob/main/docs/privacy-and-safety.md`
   - support URL: the repository Issues page
   - a screenshot: `docs/assets/demo.gif`, or a frame from it (`ffmpeg -ss 12 -i docs/assets/demo.gif -frames:v 1 shot.png`)
   - what it needs on the machine: `uv` (the bundle installs the pinned PyPI release with the host's `uv`)
2. Install the bundle yourself once in Claude Desktop (double-click the `.mcpb`) and confirm `resolve_person`
   works against `pctx demo`; the review team will do the same.
3. Complete the form linked from
   https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-server-extensions-desktop-extensions-on-claude-for-desktop
   (the "desktop extensions interest form"). State plainly that the server runs locally, stores one SQLite file,
   makes no network calls, and keeps sensitive disclosure behind an environment flag; those are the questions a
   reviewer of a personal-data extension will ask.

There is no status page. Expect an email; if nothing arrives in a few weeks, resubmit after the next release
with the new bundle URL.

## 5. Obsidian community plugin

Obsidian resolves a community plugin from a dedicated repository whose root holds `manifest.json` and whose
releases are tagged with the bare version and carry `main.js`, `manifest.json`, and `styles.css`. The release
workflow already builds and mirrors that tree; it is waiting on a repository to mirror into.

### 5a. Create the mirror repository

```bash
gh repo create JinyangWang27/obsidian-people-context --public \
  --description "Read-only Obsidian panes over a local people-context database (mirror of JinyangWang27/people-context)"
```

Leave it empty; the workflow writes it.

### 5b. Give the workflow access

Create a fine-grained personal access token at https://github.com/settings/personal-access-tokens/new:
resource owner `JinyangWang27`, repository access **only** `obsidian-people-context`, permissions
**Contents: Read and write** (the workflow pushes a commit, a tag, and a release). Then:

```bash
cd ~/code/people-context-mcp
gh variable set OBSIDIAN_PLUGIN_MIRROR_REPO --body "JinyangWang27/obsidian-people-context"
gh secret set OBSIDIAN_PLUGIN_MIRROR_TOKEN     # paste the token at the prompt
```

### 5c. Bump the plugin version and cut a release

The manifest is already fixed (`author` is your name; `authorUrl` and `description` are fine, and the `id`
contains no "obsidian"). The plugin has its own version domain; `manifest.json`, `package.json`, and
`package-lock.json` must agree, and CI checks that. Bump all three at once:

```bash
cd obsidian-plugin
npm version 0.2.0 --no-git-tag-version     # package.json + package-lock.json
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("manifest.json"); m = json.loads(p.read_text()); m["version"] = "0.2.0"
p.write_text(json.dumps(m, indent=2) + "\n")
PY
npm ci --no-audit --no-fund && npm run typecheck && npm test && npm run build
cd ..
uv run pytest -q tests/test_obsidian_plugin.py
```

Open that as its own small PR (`chore(obsidian): release 0.2.0`), merge it, then tag the merge commit on
`main`:

```bash
git checkout main && git pull
git tag obsidian-plugin-v0.2.0
git push origin obsidian-plugin-v0.2.0
gh run watch   # obsidian-plugin-release.yml
```

Expected: the workflow verifies tag = manifest version, builds twice with identical checksums, pushes the
mirror tree to `obsidian-people-context`, and creates release `0.2.0` there with the three files attached.
Confirm with `gh release view 0.2.0 --repo JinyangWang27/obsidian-people-context`.

Optional but recommended before submitting: add a `versions.json` at the mirror root mapping plugin
versions to the minimum Obsidian version, `{"0.2.0": "1.5.0"}`. The mirrored tree does not include it yet;
commit it directly in the mirror repository.

### 5d. Submit

The current process runs through the community directory site:

1. Sign in at https://community.obsidian.md and link the `JinyangWang27` GitHub account.
2. Add a plugin, pointing at `JinyangWang27/obsidian-people-context`. The directory reads `manifest.json`
   from the default branch and the matching release.
3. Address the automated review notes by pushing a new plugin version through 5c and resubmitting.

If the site instead directs you to the older pull-request route, the entry to append to
`community-plugins.json` in a fork of https://github.com/obsidianmd/obsidian-releases is:

```json
{
  "id": "people-context",
  "name": "People Context",
  "author": "Jinyang Wang",
  "description": "Read-only panes for a local-first people-context database, rendered from the pctx command-line interface.",
  "repo": "JinyangWang27/obsidian-people-context"
}
```

Review points that come up for a plugin like this one: it runs a subprocess (`pctx`), so the description and
README must say so; it is desktop-only (`isDesktopOnly: true`, already set); and it reads personal data, so
link the privacy section of [obsidian-plugin.md](obsidian-plugin.md#privacy-boundary).

### 5e. Record it

Edit [obsidian-plugin.md](obsidian-plugin.md#release-and-community-distribution): replace the paragraph that
calls mirroring "an outstanding user-operated step" with the mirror repository name and the date it was
configured, and add the directory listing URL once approved.

## After all five

- Add the Registry, Glama, and Obsidian listing URLs to the README's badges or footer.
- Re-run step 1 after every release; steps 2 to 5 update themselves from GitHub, except Smithery, which
  needs the new `.mcpb` uploaded.
