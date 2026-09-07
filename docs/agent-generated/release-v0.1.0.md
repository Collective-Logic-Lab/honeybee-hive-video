# Hive Video Tools v0.1.0

First public release of the `hive-video` package, providing reusable command-line tools and Python APIs for honey bee hive videos.

- Download archive recordings by selection, with resumable transfers and MD5 verification. Python callers can use `download_video(day=22, side=0, panel="top", target=...)` directly.
- Extract PNG frames and MP4 clips with explicit frame or nominal-second intervals, JSON provenance sidecars, output checksums, and optional bee progress telemetry.
- Run resequencing as separate reconstruction, inspection, quality-control, approval, and rendering stages. Install the optional `resequence` extra for these tools.
- Resolve FFmpeg and ffprobe automatically, or prepare and inspect them with `hive-video setup-ffmpeg` before offline work.

## Installation

Python 3.12 or newer is required.

```bash
uv tool install 'hive-video==0.1.0'
```

For resequencing support:

```bash
uv tool install 'hive-video[resequence]==0.1.0'
```

For Python use in another project, use `uv add 'hive-video==0.1.0'`, adding the `resequence` extra when needed.

## Compatibility and scope

The API is still evolving. During 0.x, incompatible interface changes receive a minor version increment and an explanation in the release notes; patch releases contain compatible fixes. Retain the selected version and dependency lockfile for reproducible analyses.

The package contains reusable video tools. Internal analyses, experiments, cluster launchers, and research data are excluded from the wheel and source distribution. The source is MIT licensed.

Full archive downloads can be large. Fragment extraction uses the nominal frame clock and preserves its documented frame-selection rules. Resequencing requires the documented inspection and QC steps; it does not establish absolute chronology or validate biological conclusions. Binary provider versions do not freeze downloadable FFmpeg bytes; setup reports the executable identity actually selected.

See the [package guide](https://github.com/Collective-Logic-Lab/honeybee-hive-video/blob/v0.1.0/docs/agent-generated/package-interface.md) and [resequencing workflow](https://github.com/Collective-Logic-Lab/honeybee-hive-video/blob/v0.1.0/docs/agent-generated/resequencing.md).
