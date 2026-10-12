# OBS Studio — Screen-Recorder Portability Source Inspector v1.0

This is a **read-only GitHub Actions inspection kit**, not a modified OBS build.

The goal is to investigate how OBS for Windows handles two directories known to appear when launching the official portable ZIP:

- `C:\ProgramData\obs-studio\shader-cache` — Direct3D shader cache
- `C:\ProgramData\obs-studio-hook` — game-capture graphics hook and Vulkan-related files

We are evaluating a **meeting recorder** that keeps Display Capture, Window Capture, microphone, and application/desktop audio working without the Game Capture deployment that the user does not need.

## Why an inspection first?

OBS's native portable mode handles profiles and scenes but does not guarantee that shared capture hooks or Direct3D shader caches avoid ProgramData. Upstream has discussed this exact problem in [OBS issue #10486](https://github.com/obsproject/obs-studio/issues/10486). The Windows `win-capture` module includes regular display/window capture and game capture, so disabling the entire module would be a mistake.

## Included files

```text
OBS-Portable-Inspection-Builder/
├── .github/
│   └── workflows/
│       └── inspect-obs-portability.yml
├── scripts/
│   └── Inspect-OBS-Portability.py
└── README.md
```

## What the GitHub workflow does

1. Checks out its own read-only inspection script.
2. Checks out a specified OBS source repo and source branch, **without modifying it**.
3. Scans C/C++, CMake and configuration source files for portability/capture code.
4. Creates a single ZIP report and human-readable inspection files.
5. Uploads the reports as a GitHub Actions artifact.

The workflow does **not** install Visual Studio, compile OBS, change registry entries, deploy hooks, or touch your laptop's ProgramData folders.

## Running

Go to **Actions → Inspect OBS Screen Recorder Portability → Run workflow**. Keep the defaults to inspect `obsproject/obs-studio` on `master`. For a clean personal fork later, change the source repository to your own `USERNAME/obs-studio`. Download the artifact named `OBS-ScreenRecorder-Portability-Inspection-<run number>` and share its `OBS-Inspection-Upload-This.zip` for review.

## Report contents

- `OBS-Inspection-README.md`: plain-English findings and warnings
- `OBS-Critical-Excerpts.md`: key source files with nearby numbered lines
- `OBS-Source-Matches.tsv`: searchable paths, line numbers and snippets
- `OBS-Inspection.json`: full structured findings
- `OBS-Next-Step.md`: proposed modifications and Windows test checklist
- `OBS-Inspection-Upload-This.zip`: nested ZIP containing the above reports

## Next stage (not included)

After reviewing the results, we can create a **separate** OBS True-Portable Builder with narrowly scoped patches and GitHub Windows compilation. We must then verify Display Capture, Window Capture, microphone and meeting audio **and** fresh ProgramData writes before replacing your existing OBS PAF skeleton.

A pure source scan cannot guarantee that disabling hooks is safe; it can locate the source of writes and help plan the patch.
