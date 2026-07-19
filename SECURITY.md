# Security Policy

## Supported versions

Security fixes are applied to the latest published release and to the current `main` branch. Older releases are not maintained unless a vulnerability requires a broader coordinated fix.

| Version | Supported |
|---|---|
| Latest published release | Yes |
| `main` | Yes, for fixes preparing the next release |
| Older releases | No |

## Reporting a vulnerability

Please report suspected vulnerabilities through [GitHub private vulnerability reporting](https://github.com/JinyangWang27/people-context-mcp/security/advisories/new).

Do not open a public issue, discussion, or pull request containing vulnerability details before coordinated disclosure. Because this project stores personal context locally, do not include real personal data, database contents, credentials, access tokens, or other secrets in a report. Use minimal synthetic examples instead.

A useful report includes:

- the affected version or commit;
- the affected component, such as the MCP server, CLI, import/export path, SQLite storage, release workflow, or plugin packaging;
- reproduction steps or a minimal proof of concept;
- the expected and observed security impact;
- any known mitigations or suggested remediation; and
- whether the issue is already public or subject to a disclosure deadline.

## Response and disclosure process

The maintainer aims to:

- acknowledge a report within 5 business days;
- provide an initial assessment within 10 business days;
- provide progress updates at least every 14 days while remediation is active; and
- coordinate a fix and public disclosure within 90 days when reasonably possible.

Timelines may change with the severity, complexity, dependency coordination, or evidence required. The reporter will be informed before public disclosure whenever practical.

Validated vulnerabilities will normally be handled through a private GitHub security advisory. The advisory may include affected versions, severity, remediation, release notes, and reporter credit when requested.

## Scope

Security reports are particularly relevant when they involve:

- unintended disclosure of stored personal context;
- bypasses of sensitivity, purpose, or operator gates;
- unsafe import, export, merge, forget, or sync behavior;
- path traversal, unsafe file permissions, or vault overwrite behavior;
- unauthenticated network exposure beyond the documented loopback boundary;
- dependency, build, release, or package-publishing compromise; or
- vulnerabilities in the distributed Claude Code or OpenClaw plugin surfaces.

General bugs, feature requests, and non-sensitive reliability issues should use the public issue tracker.
