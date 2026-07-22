# Everything_Mew GitHub README Landing Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the English-first GitHub README and its honorific Korean counterpart into a balanced landing page that serves new users and technical reviewers without changing the existing title image or product behavior.

**Architecture:** `README.md` is the source layout and is completed first. `README.ko.md` mirrors its section order, examples, links, and claims with natural honorific Korean prose. Existing detailed release, installation, SDK, validation, and safety documents remain the sources of truth; the README links to them instead of duplicating the full `v0.2.0` change list near the top.

**Tech Stack:** GitHub-flavored Markdown, limited GitHub-safe inline HTML, Shields.io badges, PowerShell verification, Python standard-library link and structure checks.

## Global Constraints

- `README.md` is the primary GitHub landing page and must be designed and reviewed first.
- `README.ko.md` must mirror the approved English structure and use honorific Korean endings (`합니다`, `됩니다`, `하세요`) in user-facing sentences.
- Preserve `docs/assets/everything-mew-banner.png` byte-for-byte with SHA-256 `D6420CEE81F3A5529FFE9255700E6C2A39378BEE54C3572DCD353C2A15882DFD` and size `705268` bytes.
- Display the existing title image centered at 420 pixels with a repository-relative path.
- Keep the four public MCP tool names and all installation commands technically accurate for `v0.2.0`.
- Do not publish machine-specific memory values, personal paths, personal email, `.env` values, API keys, or `_nonrelease/` evidence.
- Do not add PyPI, coverage, download-count, marketplace, or security-passing badges.
- Do not modify Python source, tests, package behavior, the release tag, or image assets.

---

### Task 1: Rebuild The English GitHub Landing Page

**Files:**
- Modify: `README.md`
- Reference: `docs/releases/v0.2.0.md`
- Reference: `docs/AGENT_INSTALLATION_GUIDE.md`
- Reference: `docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`

**Interfaces:**
- Consumes: release `v0.2.0`, CI workflow `.github/workflows/ci.yml`, package constraints from `pyproject.toml`, and the existing title image.
- Produces: the canonical section order and factual copy that Task 2 translates without restructuring.

- [ ] **Step 1: Replace the current English hero with the approved identity block**

Use this exact structural pattern at the top of `README.md`:

```html
<p align="center">
  <img src="docs/assets/everything-mew-banner.png" width="420" alt="Everything_Mew cat mascot helping an AI agent discover file paths">
</p>

<h1 align="center">Everything_Mew</h1>

<p align="center"><strong>A lightweight, read-only MCP bridge to Everything for fast Windows file discovery.</strong></p>

<p align="center">
  <a href="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Burntgogi/Everything_Mew/releases/tag/v0.2.0"><img alt="Release v0.2.0" src="https://img.shields.io/badge/release-v0.2.0-5865F2"></a>
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078D4">
  <img alt="Python 3.11 through 3.14" src="https://img.shields.io/badge/Python-3.11--3.14-3776AB">
  <img alt="Read-only MCP tools" src="https://img.shields.io/badge/MCP-read--only-1F883D">
  <a href="LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

<p align="center">
  <a href="README.ko.md">한국어</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#tools">Tools</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#safety-boundaries">Safety</a> ·
  <a href="#release-history">Release notes</a>
</p>
```

- [ ] **Step 2: Write the concise Overview and How it works sections**

The English copy must state that Everything_Mew:

- uses the existing local Everything index;
- returns candidate paths, not file contents;
- is Windows-only and read-only;
- recommends `everything-mew-lite` for Codex Desktop and always-enabled hosts;
- hands selected paths to normal `read`, `grep`, `ast-grep`, or LSP tools.

Show the workflow exactly once:

```text
everything_count -> everything_search -> read/grep/ast-grep/LSP on selected paths
```

- [ ] **Step 3: Add a short Quick start before detailed technical material**

The quick start must include:

```powershell
git clone https://github.com/Burntgogi/Everything_Mew.git
cd Everything_Mew
git checkout v0.2.0
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

Then show the recommended Codex configuration, copied from the public installation guide:

```toml
[mcp_servers.everything-mew]
command = "everything-mew-lite"
args = []
enabled = true
startup_timeout_sec = 20
tool_timeout_sec = 20
enabled_tools = ["everything_status", "everything_count", "everything_search", "everything_syntax_help"]

[mcp_servers.everything-mew.env]
EVERYTHING_SDK_DLL = "C:/replace/with/the/selected/sdk-dll"
```

State immediately above the TOML that the path is an example placeholder for a trusted, architecture-matching DLL from the official Everything SDK. Link to `docs/AGENT_INSTALLATION_GUIDE.md` and `docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md` for the complete safe setup. Do not claim the DLL is bundled.

- [ ] **Step 4: Reorder the remaining English sections**

Use this exact heading order after Quick start:

```text
## Tools
## Requirements
## Query compatibility and safety
## Architecture
## Usage examples
## Safety boundaries
## Validation
## Release history
## Repository contents
## Contributor notes
```

Move existing accurate details into these sections without duplicating them. Replace the current top-level `Release status` and long `Update notes` blocks with a compact `Release history` table and one paragraph linking to `docs/releases/v0.1.0.md`, `docs/releases/v0.2.0.md`, `docs/releases/v0.2.0-rc.1.md`, and `CHANGELOG.md`.

- [ ] **Step 5: Add the technical proof block without unsupported claims**

In `Validation`, preserve the existing commands and add a concise release-evidence list stating:

- four read-only tools;
- 407 pytest cases, Ruff, and strict mypy passed for `v0.2.0`;
- Windows CI covers Python 3.11 and 3.14;
- the installed wheel completed the lite MCP lifecycle without FastAPI or FastMCP;
- the `size:>100mb` E2E result is documented in `docs/releases/v0.2.0.md`.

Do not include the 13.55 MiB machine-specific memory value.

- [ ] **Step 6: Review the English diff and commit it independently**

Run:

```powershell
git diff --check
git diff -- README.md
git status --short
```

Expected: only `README.md` and the already tracked implementation-plan file differ; no image or source file is modified.

Commit:

```powershell
git add README.md
git commit -m "docs: redesign English GitHub landing page"
```

---

### Task 2: Synchronize The Honorific Korean README

**Files:**
- Modify: `README.ko.md`
- Reference: `README.md`

**Interfaces:**
- Consumes: the final heading order, code blocks, links, badges, and factual claims from Task 1.
- Produces: a Korean landing page with equivalent coverage and consistent honorific guidance.

- [ ] **Step 1: Mirror the identity block and localized navigation**

Reuse the same image and badge markup. Use this Korean tagline and navigation:

```html
<p align="center"><strong>Everything을 이용해 Windows 파일을 빠르게 찾는 가벼운 읽기 전용 MCP 연결 도구입니다.</strong></p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="#도구">도구</a> ·
  <a href="#구조">구조</a> ·
  <a href="#안전-경계">안전 경계</a> ·
  <a href="#릴리스-이력">릴리스 노트</a>
</p>
```

Use the Korean image alt text `AI 에이전트의 파일 경로 탐색을 돕는 Everything_Mew 고양이 마스코트`.

- [ ] **Step 2: Translate the approved English structure with honorific prose**

Use this exact heading order:

```text
## 개요
## 작동 방식
## 빠른 시작
## 도구
## 요구 사항
## 검색 문법 호환성과 안전
## 구조
## 사용 예시
## 안전 경계
## 검증
## 릴리스 이력
## 저장소 구성
## 기여자 참고
```

Write explanatory and instructional sentences with endings such as `합니다`, `됩니다`, `필요합니다`, and `사용하세요`. Keep commands, headings, tool names, short table cells, and diagram labels concise. Replace terse fragments such as `테스트 실행:` with complete honorific introductions such as `다음 명령으로 테스트를 실행합니다.`

- [ ] **Step 3: Keep all commands and factual claims synchronized**

The following must be byte-equivalent between language files except surrounding prose:

- clone, checkout, venv, and pip commands;
- Codex TOML block;
- four public tool names;
- query examples;
- test, Ruff, mypy, and build commands;
- release tags and links;
- badge targets and image path.

- [ ] **Step 4: Review Korean honorific consistency and commit**

Run:

```powershell
rg -n "해야 한다|하면 안 된다|사용한다|담당한다|확인한다|실행한다" README.ko.md
git diff --check
git diff -- README.ko.md
```

Expected: no unintended plain-form user guidance remains. Technical quotations or code are exempt and must be reviewed manually if matched.

Commit:

```powershell
git add README.ko.md
git commit -m "docs: synchronize honorific Korean README"
```

---

### Task 3: Verify Structure, Links, Privacy, And Rendering

**Files:**
- Verify: `README.md`
- Verify: `README.ko.md`
- Verify unchanged: `docs/assets/everything-mew-banner.png`

**Interfaces:**
- Consumes: both completed README files.
- Produces: review evidence that both entry pages are aligned, link-safe, privacy-safe, and visually usable.

- [ ] **Step 1: Verify the title image is byte-for-byte unchanged**

Run:

```powershell
$hash = (Get-FileHash docs\assets\everything-mew-banner.png -Algorithm SHA256).Hash
$size = (Get-Item docs\assets\everything-mew-banner.png).Length
if ($hash -ne "D6420CEE81F3A5529FFE9255700E6C2A39378BEE54C3572DCD353C2A15882DFD") { throw "Banner hash changed: $hash" }
if ($size -ne 705268) { throw "Banner size changed: $size" }
```

Expected: no output and exit code 0.

- [ ] **Step 2: Verify local Markdown links and required headings**

Run this standard-library checker from the repository root:

```powershell
@'
from pathlib import Path
import re

required = {
    "README.md": ["Overview", "How it works", "Quick start", "Tools", "Requirements", "Architecture", "Safety boundaries", "Validation", "Release history"],
    "README.ko.md": ["개요", "작동 방식", "빠른 시작", "도구", "요구 사항", "구조", "안전 경계", "검증", "릴리스 이력"],
}

for filename, headings in required.items():
    path = Path(filename)
    text = path.read_text(encoding="utf-8")
    actual = [match.group(1).strip() for match in re.finditer(r"^## (.+)$", text, re.MULTILINE)]
    positions = [actual.index(heading) for heading in headings]
    assert positions == sorted(positions), (filename, actual)
    for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#")):
            continue
        local = target.split("#", 1)[0]
        assert (path.parent / local).exists(), (filename, target)
print("README structure and local links passed")
'@ | python -
```

Expected: `README structure and local links passed`.

- [ ] **Step 3: Verify bilingual command and tool parity**

Run:

```powershell
@'
from pathlib import Path
import re

english = Path("README.md").read_text(encoding="utf-8")
korean = Path("README.ko.md").read_text(encoding="utf-8")
tools = {"everything_status", "everything_count", "everything_search", "everything_syntax_help"}
for text in (english, korean):
    assert tools <= set(re.findall(r"everything_[a-z_]+", text))
    assert 'command = "everything-mew-lite"' in text
    assert 'git checkout v0.2.0' in text
    assert 'docs/assets/everything-mew-banner.png' in text
print("README bilingual parity passed")
'@ | python -
```

Expected: `README bilingual parity passed`.

- [ ] **Step 4: Scan public README content for secrets and personal data**

Run:

```powershell
rg -n -i "(api[_-]?key|secret|token|password)\s*[:=]|[A-Z]:\\Users\\|@gmail\.com|@naver\.com|@daum\.net|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|_nonrelease/" README.md README.ko.md
```

Expected: no matches. If descriptive security prose contains a generic keyword, inspect it and keep only wording that cannot be mistaken for a credential.

- [ ] **Step 5: Render and inspect desktop and narrow layouts**

Use GitHub's rendered repository preview after pushing, or a GitHub-flavored Markdown renderer before pushing. Inspect at approximately 1280 pixels and 375 pixels. Confirm:

- the 420-pixel image scales down without clipping;
- badges and quick links wrap naturally;
- the first viewport communicates Windows, Everything, MCP, read-only behavior, and `everything-mew-lite`;
- code blocks do not overlap or truncate;
- English and Korean anchors navigate to the intended sections;
- dark-mode rendering keeps badge labels and image edges legible.

- [ ] **Step 6: Run repository regression checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy --strict src
git diff --check v0.2.0..HEAD
git status --short --branch
```

Expected: 407 tests pass; Ruff and strict mypy pass; no whitespace errors; only the design, plan, README, and `.gitignore` commits are present on `codex/readme-landing-redesign`.

- [ ] **Step 7: Commit any verification-only corrections**

If verification required README corrections, commit only those corrections:

```powershell
git add README.md README.ko.md
git commit -m "docs: fix README verification findings"
```

If no correction was required, do not create an empty commit.
