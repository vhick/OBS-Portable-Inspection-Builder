#!/usr/bin/env python3
"""OBS Studio source-only portability investigation.

Scans code/config for host ProgramData writes, capture components, and build
connections. It NEVER patches the checked-out source or compiles OBS.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile
from collections import Counter, defaultdict

RULES = {
    "Shader cache: direct ProgramData call": r'os_get_program_data_path(?:_ptr)?\s*\(\s*["\']obs-studio[/\\]shader-cache',
    "Shader cache: other references": r'shader-cache',
    "Graphics hook: shared ProgramData folder": r'obs-studio-hook',
    "Graphics hook: binaries and Vulkan layers": r'graphics-hook(?:32|64)?(?:\.dll)?|obs-vulkan(?:32|64)?',
    "Game Capture: source/deployment": r'game[_-]capture|GameCapture|inject[_-]library',
    "Display and Window Capture": r'monitor[_-]capture|window[_-]capture|display[_-]capture|MonitorCapture|WindowCapture',
    "Windows audio capture": r'win-wasapi|wasapi|application[_-]audio|audio[_-]output[_-]capture',
    "Native portable mode": r'portable_mode|obs_portable_mode|--portable',
    "ProgramData API references": r'programdata|program_data_path|FOLDERID_ProgramData|CSIDL_COMMON_APPDATA',
    "Optional CMake switches": r'option\s*\(\s*ENABLE_[A-Z_]+',
}

RX = {label: re.compile(expr, re.I) for label, expr in RULES.items()}
EXTENSIONS = {
    '.c', '.cc', '.cpp', '.h', '.hpp', '.cxx', '.m', '.mm', '.rc',
    '.cmake', '.txt', '.md', '.ps1', '.nsh', '.bat', '.cmd', '.ini', '.json',
    '.yaml', '.yml', '.sh', '.qrc', '.py', '.in', '.def',
}
SKIP_DIRS = {'.git', '.github', 'node_modules', '.venv', 'venv', 'build', 'build_x64', 'build_x86',
             'build_arm64', '_deps', '.cache', '.ccache'}
TARGETS = (
    'libobs-d3d11/d3d11-shader.cpp',
    'libobs-d3d11/d3d11-subsystem.cpp',
    'plugins/win-capture/CMakeLists.txt',
    'plugins/win-capture/plugin-main.c',
    'plugins/win-capture/game-capture.c',
    'plugins/win-capture/monitor-capture.c',
    'plugins/win-capture/window-capture.c',
    'plugins/win-capture/graphics-hook-info.h',
    'build-aux/steam/scripts_windows/install.bat',
    'CMakePresets.json',
)
PRIORITY_PREFIXES = ('libobs-d3d11/', 'plugins/win-capture/', 'plugins/win-wasapi/',
                     'plugins/win-dshow/', 'frontend/', 'shared/', 'build-aux/')
MAX_FILE = 1_500_000
MAX_MATCHES_PER_CATEGORY = 120


def git_metadata(root: Path) -> dict:
    def run(*args: str) -> str:
        result = subprocess.run(['git', '-C', str(root), *args],
                                text=True, capture_output=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else '(unavailable)'
    return {'commit': run('rev-parse', 'HEAD'), 'branch': run('branch', '--show-current'),
            'remote': run('remote', 'get-url', 'origin')}


def walk(root: Path):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith('.'))
        for name in sorted(names):
            path = Path(base) / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in EXTENSIONS and name not in {'CMakeLists.txt', 'Makefile', 'CMakePresets.json'}:
                continue
            try:
                if path.stat().st_size > MAX_FILE:
                    continue
                content = path.read_text(encoding='utf-8-sig', errors='replace')
                if '\x00' in content[:4096]:
                    continue
            except (OSError, UnicodeError):
                continue
            yield path.relative_to(root).as_posix(), content


def md(s: str) -> str:
    return s.replace('`', "'").replace('|', '\\|')


def get_evidence(source: Path) -> tuple[list[dict], dict, int]:
    matches = []
    counts = Counter()
    files_seen = 0
    caps = Counter()
    # Each source line may match multiple relevant categories; this is useful
    # for tracing shared capture-module dependencies without inferring them.
    for rel, contents in walk(source):
        files_seen += 1
        for n, line in enumerate(contents.splitlines(), start=1):
            for label, rx in RX.items():
                if rx.search(line):
                    counts[label] += 1
                    if caps[label] < MAX_MATCHES_PER_CATEGORY:
                        matches.append({'category': label, 'file': rel, 'line': n,
                                        'text': line.strip()[:320],
                                        'priority': rel.startswith(PRIORITY_PREFIXES)})
                        caps[label] += 1
    matches.sort(key=lambda x: (x['category'], not x['priority'], x['file'], x['line']))
    return matches, dict(counts), files_seen


def build_report(root: Path, out: Path, matches: list[dict], counts: dict, files_seen: int):
    metadata = git_metadata(root)
    present = {p: (root / p).is_file() for p in TARGETS}
    direct_shader = counts.get('Shader cache: direct ProgramData call', 0)
    hook = counts.get('Graphics hook: shared ProgramData folder', 0)
    windows = counts.get('Display and Window Capture', 0)
    audio = counts.get('Windows audio capture', 0)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    json_result = {
        'tool': 'OBS Screen-Recorder Portable Source Inspection v1.0',
        'date_utc': now, 'source': metadata, 'files_scanned': files_seen,
        'total_match_counts': counts,
        'cap_per_category': MAX_MATCHES_PER_CATEGORY,
        'target_files_found': present,
        'matches': matches,
        'analysis_constraints': ['Source-only inspection, no build or Windows runtime',
                                 'Matches are indicators, not proof of dependencies',
                                 'Capture features must be tested before removing game hooks',
                                 'No system files are modified or deleted'],
    }
    (out / 'OBS-Inspection.json').write_text(json.dumps(json_result, indent=2), encoding='utf-8')
    with (out / 'OBS-Source-Matches.tsv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(['category', 'file', 'line', 'priority', 'snippet'])
        for item in matches:
            writer.writerow([item['category'], item['file'], item['line'], item['priority'], item['text']])

    report = [
        '# OBS Studio — Screen-Recorder Portability Inspection', '',
        f'- Inspected at: `{now}`',
        f'- Source commit: `{metadata["commit"]}`',
        f'- Source repository: `{metadata["remote"]}`',
        f'- Text files scanned: **{files_seen}**',
        '- Scope: source inspection only. OBS was not compiled, patched, or executed.', '',
        '## Findings', '',
        f'- Direct shader-cache ProgramData calls: **{direct_shader}**',
        f'- `obs-studio-hook` references: **{hook}**',
        f'- Display/Window Capture references: **{windows}**',
        f'- Windows audio references: **{audio}**', '',
        'These counts are matches, **not** a verified list of all runtime write sites.', '',
        '### Interpretation', '',
        '- A shader-cache ProgramData write is a candidate for a narrowly scoped portable-path patch.',
        '- A graphics-hook installation path must be inspected separately from Game Capture source registration.',
        '- **Never remove the entire `win-capture` plugin** without proving it does not provide Display or Window Capture.',
        '- Direct3D video rendering is needed for ordinary recording even when Game Capture is unused.',
        '- Windows capture and audio source registrations must be preserved and tested.', '',
        '## Key upstream files', '',
        '| File | Present in checkout |', '|---|---|',
    ]
    report += [f'| `{md(file)}` | {"Yes" if ok else "No (inspect revision)"} |' for file, ok in present.items()]
    report += ['', '## Pattern counts', '', '| Topic | Matches |', '|---|---:|']
    report += [f'| {md(label)} | {counts.get(label,0)} |' for label in RULES]
    report += [
        '', '## Next steps', '',
        '1. Upload **this ZIP report**, not the full upstream source tree.',
        '2. Review `OBS-Critical-Excerpts.md` for the exact code paths and build connections.',
        '3. Determine if the hook deployment can be disabled *without* disabling the Windows capture module.',
        '4. Prepare a fail-closed shader-cache patch if the storage code is still supported.',
        '5. Build with the **current** upstream Windows build presets only after source inspection.',
        '6. Test OBS on Windows: Display Capture, Window Capture, desktop audio, microphone, and a saved recording.',
        '7. Check `%ProgramData%` after a clean launch, without deleting shared files used by other OBS installs.',
        '', '## Scope and caution', '',
        'No registry modifications, ProgramData cleanup, source patches, or Windows compilation were attempted.',
        'The absence of matches does not prove an API cannot write externally. The presence of a matching string',
        'does not prove it will be used on your system. Some host OS graphics caches may remain unavoidable.',
    ]
    (out / 'OBS-Inspection-README.md').write_text('\n'.join(report) + '\n', encoding='utf-8')

    evidence = ['# OBS source excerpts for manual review', '',
                'Generated from the selected upstream commit. Excerpts are presented as evidence, not patches.', '']
    for name in TARGETS:
        p = root / name
        evidence.append(f'## `{name}`')
        if not p.is_file():
            evidence.append('**File absent at this revision**. Check current upstream layout.\n')
            continue
        lines = p.read_text(encoding='utf-8-sig', errors='replace').splitlines()
        hitlines = set()
        for i, line in enumerate(lines):
            if any(rx.search(line) for label, rx in RX.items() if label not in {'Optional CMake switches'}):
                for idx in range(max(0, i - 3), min(len(lines), i + 4)):
                    hitlines.add(idx)
        # The module build file matters even if the keywords were renamed.
        if name.endswith('CMakeLists.txt'):
            hitlines.update(range(min(75, len(lines))))
        if name.endswith('CMakePresets.json'):
            hitlines.update(range(min(30, len(lines))))
        evidence.append('```text')
        for idx in sorted(hitlines)[:240]:
            evidence.append(f'{idx+1:5}: {lines[idx][:260]}')
        if len(hitlines) > 240:
            evidence.append('... excerpt truncated; consult repository for remaining matches ...')
        if not hitlines:
            evidence.append('(No configured patterns found in this file.)')
        evidence.append('```\n')
    (out / 'OBS-Critical-Excerpts.md').write_text('\n'.join(evidence), encoding='utf-8')

    decisions = [
        '# Patch-readiness review — NOT a build authorization', '',
        '**Goal:** A meeting/screen-recording OBS edition that retains display, window, and audio sources.', '',
        '## Proposed changes for a later builder', '',
        '1. Redirect OBS-owned D3D11 shader cache to a writable application-relative location.',
        '2. Avoid installing shared game-capture hook files into `C:\\ProgramData\\obs-studio-hook` **only if** the remaining screen/window/audio capture works independently.',
        '3. Keep native `--portable` OBS configuration handling and avoid deleting existing settings.', '',
        '## Do not do these yet', '',
        '- Do not remove `plugins/win-capture` wholesale; it includes Display and Window Capture.',
        '- Do not remove `libobs-d3d11`: the renderer is needed for screen recording.',
        '- Do not delete system-wide Vulkan layers, hook DLLs, or shader cache on an existing PC.',
        '- Do not replace a working PAF until a built experimental EXE passes a Windows runtime test.', '',
        '## Required Windows test matrix', '',
        '| Test | Result |', '|---|---|',
        '| First launch with isolated ProgramData backup | Not tested |',
        '| Display Capture of desktop | Not tested |',
        '| Window Capture of browser / Meet | Not tested |',
        '| Browser audio recorded | Not tested |',
        '| Microphone recorded | Not tested |',
        '| MKV recorded and playable | Not tested |',
        '| OBS scenes and preferences preserved | Not tested |',
        '| AppData / ProgramData creation rechecked | Not tested |',
        '| PAF upgrade retains settings | Not tested |', '',
        'Review the reports and approve the patch/build approach before moving to the build stage.',
    ]
    (out / 'OBS-Next-Step.md').write_text('\n'.join(decisions) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if not (root / 'CMakeLists.txt').is_file() or not (root / 'libobs-d3d11').is_dir():
        parser.error('Not a recognizable OBS Studio source checkout; expected root CMakeLists.txt and libobs-d3d11/')
    output.mkdir(parents=True, exist_ok=True)
    matches, counts, total = get_evidence(root)
    build_report(root, output, matches, counts, total)
    included = sorted(p for p in output.iterdir() if p.is_file())
    bundle = output / 'OBS-Inspection-Upload-This.zip'
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in included:
            archive.write(p, arcname=p.name)
    print(f'PASS: inspected {total} files; produced {len(included)} reports and {bundle.name}')
    print('Found:', dict(counts))
    print('REPORT:', output / 'OBS-Inspection-README.md')
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        with open(summary_path, 'a', encoding='utf-8') as f:
            f.write((output / 'OBS-Inspection-README.md').read_text(encoding='utf-8')[:25000])


if __name__ == '__main__':
    main()
