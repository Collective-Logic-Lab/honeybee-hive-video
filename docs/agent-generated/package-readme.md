# Hive Video Tools

`hive-video` provides command-line tools and Python APIs for downloading, resequencing, and extracting fragments from honey bee hive videos. It is part of the [Collective Logic Lab's honeybee-hive-video project](https://github.com/Collective-Logic-Lab/honeybee-hive-video).

- `download` selects a recording from the Edmond 2019 honey bee video archive, resumes partial transfers, and verifies the archive MD5 checksum.
- `fragment` extracts a PNG frame or an MP4 clip from a local video and writes a JSON sidecar with provenance and an output checksum.
- `resequence` provides individual reconstruction, inspection, quality-control, and rendering stages. Its dependencies are optional.

Version 0.1.0 is the first public package release. These tools have been used in research; the packaged API is still evolving. During 0.x, incompatible interface changes receive a minor version increment and release notes. Retain a dependency lockfile for reproducible work.

## Install

Use Python 3.12 or newer. To install the command-line tools with `uv`:

```bash
uv tool install hive-video
hive-video --help
```

To include resequencing support, install with the optional extra:

```bash
uv tool install 'hive-video[resequence]'
```

FFmpeg and ffprobe are resolved automatically. The tools use an explicitly configured pair or a complete pair on `PATH`; otherwise, the included provider downloads and caches platform-specific builds from the FFmpeg 8 family. Prepare them before offline work with:

```bash
hive-video setup-ffmpeg
```

This reports their executable paths, versions, and checksums. Help and archive downloading do not need FFmpeg.

## Extract a fragment

Given a local `source.mp4`, extract its first 25 frames:

```bash
hive-video fragment --video source.mp4 --start-frame 0 --duration-frames 25 --out clip.mp4
```

Omit the duration and use a `.png` output to extract one frame. Existing output files are refused. The bee progress display can be changed with `--progress plain` or disabled with `--progress off`.

## Use from Python

Add the library to the project that will import it:

```bash
uv add hive-video
```

Use `uv add 'hive-video[resequence]'` if you also need resequencing. Then download a recording and extract a clip:

```python
from pathlib import Path

from hive_video.download import download_video
from hive_video.fragment import create_fragment

my_dir = Path("data/day22")
source = download_video(day=22, side=0, panel="top", target=my_dir)
clip = create_fragment(source, my_dir / "clip.mp4", start=0, duration=30, unit="seconds")
print(clip)
```

`day=22` selects the archive's sequential `start22` capture identifier; the filename gives its calendar timestamp. Downloading retrieves the entire source recording, which can be tens of gigabytes. Seconds use the source's nominal frame clock. See the [package interface guide](https://github.com/Collective-Logic-Lab/honeybee-hive-video/blob/main/docs/agent-generated/package-interface.md) for selectors, transfer settings, progress callbacks, and fragment conventions.

## Resequencing and scientific scope

Resequencing is computationally expensive and includes human inspection and join quality control. Follow the [stage workflow](https://github.com/Collective-Logic-Lab/honeybee-hive-video/blob/main/docs/agent-generated/resequencing.md) and [methods record](https://github.com/Collective-Logic-Lab/honeybee-hive-video/blob/main/docs/agent-generated/METHODS.md). Reconstruction does not independently establish absolute recording chronology or validate a biological interpretation.

This distribution contains the reusable video tools. Internal analyses, experimental recipes, Slurm launchers, and research data remain in the source project.

The package source is MIT licensed. FFmpeg executables are supplied separately by the selected system installation or binary provider.
