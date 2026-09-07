# Hive Video Tools v0.1.1

Adds `hive-video --version` and `python -m hive_video --version`, which print `hive-video 0.1.1` and exit successfully without requiring a subcommand or preparing FFmpeg.

The notebook documentation now shows installation into the current Python kernel and version checking with the existing `hive_video.__version__` attribute. This is a compatible interface fix; video processing, dependencies, and output formats are unchanged.

After this release is published, upgrade an existing CLI installation with `uv tool upgrade hive-video`.
