# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1](https://github.com/JinyangWang27/people-context/compare/v1.1.0...v1.1.1) (2026-09-03)


### Bug Fixes

* **mcp:** flag refusals as tool errors and publish the alias kind enum ([#124](https://github.com/JinyangWang27/people-context/issues/124)) ([a9d891f](https://github.com/JinyangWang27/people-context/commit/a9d891feac3a92bb9d7c4dac78fff50e32893932)), closes [#117](https://github.com/JinyangWang27/people-context/issues/117)
* **registry:** match GitHub login casing and add package version ([#122](https://github.com/JinyangWang27/people-context/issues/122)) ([66dab47](https://github.com/JinyangWang27/people-context/commit/66dab47bf510fd8dc43e4882e0a42da176c2c1b8))

## [1.1.0](https://github.com/JinyangWang27/people-context/compare/v1.0.0...v1.1.0) (2026-09-03)


### Features

* adoption and onboarding — README pitch, pctx setup, one-call remember, prompts ([#116](https://github.com/JinyangWang27/people-context/issues/116)) ([ad9f3d9](https://github.com/JinyangWang27/people-context/commit/ad9f3d948fbe8ab98ebf65fa2e09eef1a8a8fb6d))
* **cli:** expose the import lifecycle through bounded pctx commands (M16.1) ([#95](https://github.com/JinyangWang27/people-context/issues/95)) ([5c243fa](https://github.com/JinyangWang27/people-context/commit/5c243fa6cda00ebdddd6c96486e0cca6e43127cf))
* **imports:** add durable source claims, commit mappings, and bootstrap v2 (M18.1) ([#99](https://github.com/JinyangWang27/people-context/issues/99)) ([7188878](https://github.com/JinyangWang27/people-context/commit/71888784c10edd7355ecd2c853644777850bd416))
* **imports:** add the streaming source reader and parser-work budget (M20.1) ([#105](https://github.com/JinyangWang27/people-context/issues/105)) ([b43e075](https://github.com/JinyangWang27/people-context/commit/b43e07515dfa687b8d03e8551491720313dbea4b))
* **imports:** expand and bound strict candidates to observation, trait, and relationship (M17.1) ([#97](https://github.com/JinyangWang27/people-context/issues/97)) ([f4515e7](https://github.com/JinyangWang27/people-context/commit/f4515e736f71c872c37313bcd12715b637d54f56))
* **imports:** expose bounded source provenance and inspection (M18.2) ([dad64c9](https://github.com/JinyangWang27/people-context/commit/dad64c9cd7e0e20a50f0e08fc4b6ca5aec96e3a8))
* **imports:** expose bounded source provenance and inspection (M18.2) ([#100](https://github.com/JinyangWang27/people-context/issues/100)) ([8bc2b3f](https://github.com/JinyangWang27/people-context/commit/8bc2b3f7491a90ca488fd165b4cb4075ceb4b16f))
* **imports:** resolve WhatsApp exports in two bounded passes (M20.3) ([#111](https://github.com/JinyangWang27/people-context/issues/111)) ([6d7145c](https://github.com/JinyangWang27/people-context/commit/6d7145c78df5d36fec77898965b20ae512c8a77b))
* **imports:** stage agent-extracted candidates from the CLI (M17.2) ([#98](https://github.com/JinyangWang27/people-context/issues/98)) ([7607473](https://github.com/JinyangWang27/people-context/commit/76074736251549acd129753ca6d211af0379ce4a))
* **imports:** stream mbox messages and meter address expansion (M20.2) ([0f42d2d](https://github.com/JinyangWang27/people-context/commit/0f42d2dbae131240724f667143bf9aef393831ed))
* **imports:** stream mbox messages and meter address expansion (M20.2) ([#108](https://github.com/JinyangWang27/people-context/issues/108)) ([7951dba](https://github.com/JinyangWang27/people-context/commit/7951dbaa4c45426f1cdedf3bd4d2b63a66bd4018))
* **insights:** add bounded person timeline reads (M19.1) ([d421e4a](https://github.com/JinyangWang27/people-context/commit/d421e4a4d0e9eccfeec86cabfdaa373b17aecc2d))
* **insights:** add bounded person timeline reads (M19.1) ([#102](https://github.com/JinyangWang27/people-context/issues/102)) ([c079ee6](https://github.com/JinyangWang27/people-context/commit/c079ee6c410cf898aaa8cd54030d0126e863beb3))
* **insights:** add consolidation context and atomic fact supersession (M19.2) ([151074a](https://github.com/JinyangWang27/people-context/commit/151074a5905cf71c6359266390b151810764289c))
* **insights:** add consolidation context and atomic fact supersession (M19.2) ([319f15e](https://github.com/JinyangWang27/people-context/commit/319f15ed02676cc2a9556681d7fd27470fd279f6))
* **traits:** ground traits in durable evidence records and bootstrap v3 (M18.3) ([04aee5f](https://github.com/JinyangWang27/people-context/commit/04aee5f074f537be2e197326a160dbc9a63e9275))


### Bug Fixes

* advertise the server version and create databases owner-only ([#112](https://github.com/JinyangWang27/people-context/issues/112)) ([b5316c6](https://github.com/JinyangWang27/people-context/commit/b5316c672c2bd10106a150d3da34b32d7e21c512))
* **imports:** bound the address parser's intermediate and free the source buffer ([fc3df13](https://github.com/JinyangWang27/people-context/commit/fc3df13a8a667087b91dc0e74c999edf244ef764))
* **imports:** charge the unparsed header before the policy expands it ([ef8ab96](https://github.com/JinyangWang27/people-context/commit/ef8ab96cecf9e2ffd1eb0fb4122842fb0c3889f0))
* **imports:** keep sort keys and identifier shape out of the cursor contract ([8ae178b](https://github.com/JinyangWang27/people-context/commit/8ae178b54c12e5fe7e91b7fea107efec4bcc836e))
* **imports:** meter one header's address expansion before it is parsed ([bfcff65](https://github.com/JinyangWang27/people-context/commit/bfcff65bb6147c21a424f4938a2fe5d51bd6c416))
* **imports:** scope inspection cursors and warn in both output modes ([c708bc7](https://github.com/JinyangWang27/people-context/commit/c708bc7c65130fd80bfa668a169a82a2ace10b05))
* **imports:** seek the listing index and stop bounding identifiers ([a7c9b93](https://github.com/JinyangWang27/people-context/commit/a7c9b934a8fbc0dcda5b5ad377a896ed810cebde))
* **insights:** keep timeline evidence resolvable and non-disclosing ([4a3e918](https://github.com/JinyangWang27/people-context/commit/4a3e9189dfe01ea01043913d0d846d89f323d232))
* **insights:** order the timeline exactly and bound every branch ([eaa0e0d](https://github.com/JinyangWang27/people-context/commit/eaa0e0da978d583f97ece10ca045c1fe38bd675e))
* **insights:** report stored provenance for every consolidation record ([b951fd3](https://github.com/JinyangWang27/people-context/commit/b951fd3a59f665cd20e173e4a6860114d6a263be))
* refuse a symlink raced into the resolved database path ([#113](https://github.com/JinyangWang27/people-context/issues/113)) ([fe6cd56](https://github.com/JinyangWang27/people-context/commit/fe6cd5661d8a765ff37d44f34def873277a19a0a))


### Documentation

* **import:** describe both header expansions the budget charges ([4db7b07](https://github.com/JinyangWang27/people-context/commit/4db7b0757e7995511cfb834e95eeb1e14df86d77))

## [1.0.0](https://github.com/JinyangWang27/people-context/compare/v0.4.0...v1.0.0) (2026-08-25)


### Bug Fixes

* **ci:** refresh uv.lock on release pull requests ([b0acf5f](https://github.com/JinyangWang27/people-context/commit/b0acf5ff200f0776dc6cbc2a61fe718e05a16de0))
* resolve five review findings across tests, typing, CI, and graph traversal ([#86](https://github.com/JinyangWang27/people-context/issues/86)) ([8daf955](https://github.com/JinyangWang27/people-context/commit/8daf9552632f0664e1619520a464eb26a544f205))


### Documentation

* plan M16-M19 roadmap ([bb7db50](https://github.com/JinyangWang27/people-context/commit/bb7db504516b6dcb9c8ccc3c79943dc2d5c845cc))


### Miscellaneous Chores

* request the 1.0.0 milestone ([c9dde93](https://github.com/JinyangWang27/people-context/commit/c9dde93695ad42de8d2cc3e26adcd5b9105423cc))

## [0.4.0](https://github.com/JinyangWang27/people-context/compare/v0.3.0...v0.4.0) (2026-08-21)


### Features

* add a read-only Obsidian plugin and mirrored release (M14.4) ([#78](https://github.com/JinyangWang27/people-context/issues/78)) ([a3ae888](https://github.com/JinyangWang27/people-context/commit/a3ae8881df4703593f27913c740cd2e1b0cb0f2c))
* add a reproducible eval harness and use-case gallery (M15.4) ([#83](https://github.com/JinyangWang27/people-context/issues/83)) ([012aa7a](https://github.com/JinyangWang27/people-context/commit/012aa7ab6f1d56284ea5e6499e7fc9833b13d997))
* add meeting-prep skill flow and private reminder ICS export (M13.3) ([#71](https://github.com/JinyangWang27/people-context/issues/71)) ([f3c5b33](https://github.com/JinyangWang27/people-context/commit/f3c5b33b8b7adf8d055fea8488427b12292608d5))
* add opt-in SQLCipher at-rest encryption (M12.4) ([#68](https://github.com/JinyangWang27/people-context/issues/68)) ([653a385](https://github.com/JinyangWang27/people-context/commit/653a3850bce043df6879226cf97e85b36bf3dbb3))
* add Outlook contacts and WhatsApp chat import extractors (M14.3) ([#77](https://github.com/JinyangWang27/people-context/issues/77)) ([e332dc2](https://github.com/JinyangWang27/people-context/commit/e332dc21c09149a01dc816550db7e51445e84dab))
* add stable person brief and person-index JSON (M14.1) ([f4e14d1](https://github.com/JinyangWang27/people-context/commit/f4e14d14c00b8caa9a6806654f45765ca7ccd201))
* **cli:** deterministic vCard export (M14.2) ([#74](https://github.com/JinyangWang27/people-context/issues/74)) ([5c79375](https://github.com/JinyangWang27/people-context/commit/5c7937573b956baca387f21a41dfeb58380faf8d))
* **cli:** export active reminders as a private iCalendar file (M13.3) ([2f25b91](https://github.com/JinyangWang27/people-context/commit/2f25b91abb24621958a0c5ae6ebf9a8c0e4c9bd4))
* **cli:** follow the local changelog as deterministic JSON lines (M13.4) ([a146028](https://github.com/JinyangWang27/people-context/commit/a1460282a811378efea2fc95fa9373c484ee8f7b))
* explain which stored name produced an exact match (M15.3) ([#82](https://github.com/JinyangWang27/people-context/issues/82)) ([ed83f24](https://github.com/JinyangWang27/people-context/commit/ed83f2490c7bac077885977b3ee90481450430ed))
* export strict bootstrap sync bundles (M11.2) ([#52](https://github.com/JinyangWang27/people-context/issues/52)) ([a82ec97](https://github.com/JinyangWang27/people-context/commit/a82ec979c88ad11a68250cc85f4bd5c5b3ad80d5))
* follow the local changelog with a deterministic tail (M13.4) ([6d6fe9e](https://github.com/JinyangWang27/people-context/commit/6d6fe9eebbe47c5bbbe97297a43f8abb00fb8db3))
* report an aggregate-only local inventory (M15.2) ([866b208](https://github.com/JinyangWang27/people-context/commit/866b2080958647393758ac25031267519efdd7f4))
* report deterministic data-quality findings (M15.1) ([5c591ed](https://github.com/JinyangWang27/people-context/commit/5c591ed27c18d1f6a3a9cd35937a8c2f28e185ee))
* report stale relationships over ordinary interactions (M13.1) ([71be5d7](https://github.com/JinyangWang27/people-context/commit/71be5d7ccf7c2e32335635764e81280277688f56))
* report upcoming birthdays and reminders (M13.2) ([7abd6b3](https://github.com/JinyangWang27/people-context/commit/7abd6b355a6c6fd2b5ee4c67d222a739e8c419e0))
* restore bootstrap sync bundles into fresh databases (M11.3) ([#53](https://github.com/JinyangWang27/people-context/issues/53)) ([b17254b](https://github.com/JinyangWang27/people-context/commit/b17254b7243ad7a07fee2286b0fade3cbc9919fc))
* support unbounded changelog reads (M11.1) ([#47](https://github.com/JinyangWang27/people-context/issues/47)) ([910cb0d](https://github.com/JinyangWang27/people-context/commit/910cb0d18c075bab0ccf4cb437d47543df6b0747))


### Bug Fixes

* break same-millisecond recency ties by stored timestamp ([fe6b7c7](https://github.com/JinyangWang27/people-context/commit/fe6b7c7c1b3572e6270ca7d041c9aae83721c5fb))
* **cli:** compare database entries by filesystem identity too ([14fedc7](https://github.com/JinyangWang27/people-context/commit/14fedc7049d7b1f9d166467363ea028a973c75ae))
* **cli:** guard the active database and order recurring VTODO times ([c85d439](https://github.com/JinyangWang27/people-context/commit/c85d439612d8acf1a91b4da88eb2cc8ebe8f0fa8))
* **cli:** name the probed file exactly and check the store's identity ([71cfbff](https://github.com/JinyangWang27/people-context/commit/71cfbff38051342693dbf2856ab409561be715f6))
* **cli:** refuse a stats target that opening would migrate ([91be8d5](https://github.com/JinyangWang27/people-context/commit/91be8d5df49039b0fd6cccbf1da41be0b0dbfc74))
* **cli:** refuse to measure a database stats would have to create ([a4bc9ce](https://github.com/JinyangWang27/people-context/commit/a4bc9ce29f1b9be3cb3869812391b2b675fedb29))
* **cli:** reserve the resolved target of a symlinked database ([4ef693a](https://github.com/JinyangWang27/people-context/commit/4ef693a679ad7f0f64f1255f9eca7a0f21f6453f))
* **cli:** warn before doctor evidence and declare incomplete actions ([9775cd0](https://github.com/JinyangWang27/people-context/commit/9775cd00a24707e57affe4d696da42043afd1c78))
* compare interaction timestamps by instant in the staleness report ([d4cf959](https://github.com/JinyangWang27/people-context/commit/d4cf959bbe03c458a0bfb39f23a993f68e9a26ad))
* **exports:** anchor recurrence on the deadline's own calendar fields ([8acf012](https://github.com/JinyangWang27/people-context/commit/8acf012b3b46905635a422ba51745c412d0ddf7f))
* **exports:** anchor recurring VTODOs and reject invalid TEXT controls ([417d260](https://github.com/JinyangWang27/people-context/commit/417d2601cf1474f87d1e147d158092e89efc56fb))
* fold authored bucket keys and read one stats snapshot ([7fa5566](https://github.com/JinyangWang27/people-context/commit/7fa556600f42afeb9fd1037aa2504344ba27b639))
* **mcp:** complete SDK v2 migration ([#65](https://github.com/JinyangWang27/people-context/issues/65)) ([27c60e5](https://github.com/JinyangWang27/people-context/commit/27c60e5ea0b66fd6fc24f2176c003501a4b7476a))
* pseudonymize device ids by provenance rather than by shape ([f32fbc1](https://github.com/JinyangWang27/people-context/commit/f32fbc1a99d6ba13dfb2f0744d7695a35200261f))
* pseudonymize restored device ids and resolve the measured path ([1604767](https://github.com/JinyangWang27/people-context/commit/1604767112a183b8b2aa1b7fc5fdff282b7cb9fe))
* **release:** handle unchanged release PR ([#66](https://github.com/JinyangWang27/people-context/issues/66)) ([bcf3257](https://github.com/JinyangWang27/people-context/commit/bcf32576d2c76bcedef7049ebe1a2371228d57e7))
* select the latest interaction by exact parsed instant ([55c28d7](https://github.com/JinyangWang27/people-context/commit/55c28d7361469a85b54b950d8970cc4c4e63e181))


### Documentation

* add dated threat comparison and README demo (M12.3) ([8c420a9](https://github.com/JinyangWang27/people-context/commit/8c420a9cd9ca91312f381ac21460ba7c592ef7dd))
* add meeting preparation to the usage skill (M13.3) ([cbe1f2d](https://github.com/JinyangWang27/people-context/commit/cbe1f2d70ab3e1cd54e0910cf3e241550be469a7))
* mark the changelog watch checklist item delivered (M13.4) ([8ff5fd3](https://github.com/JinyangWang27/people-context/commit/8ff5fd32311fbe8ab9d333f8aea2689d4ff12407))
* publish compatibility promise (M12.1) ([#55](https://github.com/JinyangWang27/people-context/issues/55)) ([f8ef68b](https://github.com/JinyangWang27/people-context/commit/f8ef68b79c57903db21b74535647e9b174eb885d))
* source every threat-comparison axis per vendor ([6ffc291](https://github.com/JinyangWang27/people-context/commit/6ffc291064dd5f3967c6eb3b2091004115cf3773))

## [0.3.0] - 2026-07-23

### Changed

- Made the package-aligned `people-context` command launch the MCP server and retained `people-context-mcp` as an
  equivalent server alias.
- Renamed the human-operated CLI to the concise `pctx` command.
- Pointed MCP Registry metadata at the primary `people-context` PyPI distribution.
- Bumped the repository-coupled Codex plugin manifest to `0.3.0`.

### Removed

- Removed the legacy `people-context-mcp` PyPI compatibility distribution and its release job.

## [0.2.0] - 2026-07-23

### Added

- Codex plugin packaging with local marketplace metadata and validation.
- MCP Registry and community-directory metadata with reproducible `uvx` launch configuration.
- Native-UV MCPB bundle and setup guides for supported desktop clients and editors.
- Optional non-root Docker image and tag-triggered GitHub Container Registry publishing.
- ICS calendar attendee imports through the staged review and commit workflow.
- LinkedIn Connections CSV imports with preamble-aware, offline parsing.
- `people-context init` for safely importing supported contact files into a reviewed staged batch.
- `people-context demo [--reset]` for exploring an isolated database with deterministic sample data.
- A packaged usage skill that teaches agents privacy-aware People Context tool composition.
- User-invocable Claude Code workflows for `/people-context:who`, `/people-context:remember`, and
  `/people-context:reminders`.

### Changed

- Made the zero-clone `uvx` installation path the primary quick-start flow.
- Moved import extractor routing into its own adapter boundary without changing import behavior.
- Reorganized application, adapter, CLI, and persistence code by capability around a shared runtime composition root,
  with automated architecture-boundary enforcement.
- Bumped the Claude Code and OpenClaw plugins to `0.2.0` and the Registry compatibility package to `0.1.0.post2`.
- Updated development, runtime, and GitHub Actions dependencies.

### Security

- Added CodeQL analysis and strengthened dependency and workflow pinning.
- Patched vulnerable OpenClaw dependencies and replaced trailing-slash regex processing with a linear scan.

## [0.1.1] - 2026-07-19

### Changed

- Published the first release under the renamed `people-context` distribution.

## [0.1.0] - 2026-07-18

### Added

- Initial local-first People Context MCP server release.

[0.3.0]: https://github.com/JinyangWang27/people-context/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/JinyangWang27/people-context/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/JinyangWang27/people-context/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/JinyangWang27/people-context/releases/tag/v0.1.0
