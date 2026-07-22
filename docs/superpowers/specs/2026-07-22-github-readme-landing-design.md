# Everything_Mew GitHub README Landing Design

**Status:** Approved direction: balanced user and reviewer experience.

**Reference:** [`Burntgogi/Gpt_Codex_HWP`](https://github.com/Burntgogi/Gpt_Codex_HWP) for its full-width identity block, evidence badges, quick navigation, and proof-before-detail hierarchy. The new page must retain Everything_Mew's own identity and must not imitate the reference repository's document-product messaging or visual assets.

## Objective

Redesign the first screen and information hierarchy of `README.md` and `README.ko.md` so that:

1. a new Codex or MCP user understands the product, platform, safety model, and next action within the first screen;
2. a technical reviewer can immediately find release, CI, compatibility, architecture, validation, and security evidence;
3. the existing detailed SDK and query documentation remains available without dominating the introduction; and
4. English and Korean pages remain structurally equivalent and easy to maintain.

`README.md` is the primary GitHub landing page and is designed and reviewed
first. `README.ko.md` follows the approved English structure and uses consistent
Korean honorific prose (`합니다` and `하세요`) for all user-facing guidance.

## Audiences

- **Primary:** Codex Desktop and other local MCP users who want fast Windows file discovery.
- **Secondary:** MCP developers, security reviewers, and maintainers validating the runtime and Everything SDK integration.

The README should lead with user value, then place technical proof immediately after it. Neither audience receives a separate landing page.

## Design Direction

Use the selected balanced layout with stronger proof signals from the evidence-first alternative:

- centered visual identity;
- one-sentence product definition;
- a compact row of factual badges;
- language and section jump links;
- a three-step discovery workflow;
- a short quick-start path;
- release and validation evidence close to the top;
- detailed update, SDK, safety, and contributor material lower in the page.

Avoid marketing claims such as "fastest," "zero cost," or an unqualified memory reduction. Do not place machine-specific memory measurements in a badge. The validated 407-test count may appear as release evidence linked to `docs/releases/v0.2.0.md`, but it must not be presented as a continuously updated CI counter.

## Visual Identity

### Banner

Use the existing repository asset
`docs/assets/everything-mew-banner.png` without regenerating, editing, cropping,
or recompressing it:

- preserve its 900 x 620 dimensions, mascot, magnifying-glass motif, grid,
  colors, title, and subtitle;
- keep the current centered display width of 420 pixels so the relatively tall
  image does not dominate the first viewport;
- use a repository-relative image path instead of a raw-branch URL;
- provide meaningful English alt text in `README.md` and Korean alt text in
  `README.ko.md`.

### Palette And Tone

- Use the mascot's white, lavender, pale blue, and small pink accents.
- Keep text and evidence sections neutral; do not turn the README into a purple-only theme.
- Preserve a quiet technical tone. The mascot is a recognition device, not a substitute for product explanation.

## Top-Of-Page Structure

Both language files use this exact order:

1. centered existing title image at 420 pixels wide;
2. centered `Everything_Mew` H1;
3. centered one-sentence product definition;
4. factual badge row;
5. language and quick-navigation row;
6. concise overview;
7. `count -> search -> inspect` workflow;
8. minimal quick start;
9. release and validation proof;
10. detailed reference sections.

### Badge Set

Use only badges backed by repository state:

- CI badge linked to `.github/workflows/ci.yml`;
- release badge linked to GitHub release `v0.2.0`;
- Windows badge;
- Python `3.11-3.14` badge, matching `pyproject.toml`;
- Apache-2.0 badge linked to `LICENSE`;
- read-only badge expressed as a product property, not a security certification.

Do not add a security-passing badge because the repository has no dedicated security workflow. Do not add PyPI, download-count, coverage, or marketplace badges until those services actually exist.

### Quick Navigation

English:

`한국어 · Quick start · Tools · Architecture · Safety · Release notes`

Korean:

`English · 빠른 시작 · 도구 · 구조 · 안전 경계 · 릴리스 노트`

Every anchor must be verified against GitHub's generated heading IDs in its respective language file.

## Information Architecture

### First Reading Path

The first reading path should answer five questions in order:

1. **What is it?** A lightweight, read-only MCP bridge to the local Everything index.
2. **Why use it?** It finds candidate paths before more expensive content inspection.
3. **How does it work?** `everything_count -> everything_search -> normal read/code tools`.
4. **How do I start?** Install the release wheel or package and register `everything-mew-lite`.
5. **Can I trust the release?** Link to CI, release notes, four-tool read-only contract, and validation results.

### Proposed Main Section Order

1. Overview / 개요
2. How it works / 작동 방식
3. Quick start / 빠른 시작
4. Tools / 도구
5. Requirements / 요구 사항
6. Query compatibility and safety / 검색 문법 호환성과 안전
7. Architecture / 구조
8. Validation / 검증
9. Release history / 릴리스 이력
10. Repository contents / 저장소 구성
11. Contributor notes / 기여자 참고

The current long `0.2.0` update-note list moves out of the introduction. Replace it with a short release summary and links to `docs/releases/v0.2.0.md` and `CHANGELOG.md`. Do not delete detailed release evidence from those documents.

## Quick Start Content

The quick start must be short enough to scan and must not imply that the Everything SDK DLL is bundled.

It should contain:

1. Windows, running Everything, supported CPython, and matching Everything SDK DLL prerequisites;
2. runtime-only installation for the lite path;
3. one Codex/local stdio registration example using `everything-mew-lite` or `python -m everything_mcp.lite_stdio`;
4. one status call and one safe scoped search example;
5. links to the full agent and SDK installation guides.

The optional FastMCP path belongs in an expandable detail block or a compatibility subsection after the recommended lite setup.

## Proof And Safety Presentation

Present technical proof as short, sourced statements:

- four read-only tools;
- direct stdio lite runtime does not require FastAPI, FastMCP, or the MCP SDK;
- Python 3.11 and 3.14 Windows CI matrix;
- 407 pytest cases, Ruff, and strict mypy for the `v0.2.0` release;
- installed-wheel MCP lifecycle and Everything SDK E2E results;
- broad-query, scope-boundary, timeout, and SDK-state safeguards.

Machine-specific memory values remain in local release evidence, not in public badges or evergreen headline copy. Public prose may say "lower-standby lite path" because the architecture and release notes support that claim.

## Bilingual Maintenance

- `README.md` remains English-first and links to `README.ko.md`.
- `README.ko.md` remains Korean-first and links to `README.md`.
- English copy and section order are finalized first; Korean is synchronized
  from that approved structure rather than independently reorganized.
- Korean user-facing guidance consistently uses honorific endings such as
  `합니다`, `됩니다`, and `하세요`; terse labels, table cells, commands, and
  code remain concise where full sentences are unnecessary.
- Both files use the same section order, badge order, code examples, links, and factual claims.
- Heading wording may differ naturally by language, but quick links must target the correct localized anchors.
- A review checklist must compare both heading trees and every code block before merge.

## GitHub Rendering Constraints

- Use GitHub-compatible Markdown and limited inline HTML already accepted by GitHub README rendering.
- Do not use JavaScript, external CSS, Mermaid as the hero, collapsible content for required installation steps, or layout tables solely for decoration.
- Keep badges and navigation useful on narrow screens by allowing natural wrapping.
- Verify that the banner does not dominate the first mobile viewport.
- All images require descriptive alt text and repository-relative paths.
- Avoid fragile raw-branch image URLs when a relative path works.

## Acceptance Criteria

1. English and Korean README pages have equivalent heading order and content coverage.
2. The first screen identifies Windows, Everything, MCP, read-only behavior, and the recommended lite runtime.
3. Quick start appears before detailed release notes and SDK syntax discussion.
4. Every badge and numerical claim has a repository or release-note source.
5. No machine-specific memory number appears as an evergreen badge or headline claim.
6. The existing banner is unchanged, uses a relative path, and displays without
   clipping at desktop and narrow GitHub widths.
7. All internal links and localized heading anchors resolve.
8. Code blocks preserve valid PowerShell, JSON, and command syntax.
9. README changes introduce no personal email, local path, `.env` value, API key, or unpublished release evidence.
10. Markdown lint or equivalent structural checks pass, and the rendered GitHub preview is inspected in both desktop and narrow layouts.

## Out Of Scope

- changing MCP behavior, package APIs, or the Everything SDK adapter;
- publishing to PyPI;
- changing the mascot concept or project name;
- generating, editing, cropping, or recompressing the existing title image;
- adding unsupported platforms or compatibility claims;
- creating a separate marketing website;
- exposing `_nonrelease/` evidence.
