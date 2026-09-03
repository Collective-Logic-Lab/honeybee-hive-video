# Honeybee Hive Video

> This documentation is a work-in-progress.

This folder contains video-processing and analysis tools for the [https://collective-logic-lab.github.io/](Collective Logic Lab's) honey bee collective behavior research project.

The current work focuses on comb-building and festoon-related behavior in the "Videos for honey bee lifetime tracking data 2019" dataset published by Smith et al. (2019), with related context from Neubauer et al. (2023), "Honey Bee Drones Are Synchronously Hyperactive inside the Nest." DOIs: [10.17617/3.LLWRWR](https://doi.org/10.17617/3.LLWRWR) for the dataset and [10.1016/j.anbehav.2023.05.018](https://doi.org/10.1016/j.anbehav.2023.05.018) for the paper.

The videos are here: [https://edmond.mpg.de/dataset.xhtml?persistentId=doi:10.17617/3.LLWRWR](https://edmond.mpg.de/dataset.xhtml?persistentId=doi:10.17617/3.LLWRWR).

**Data Companion: Huggingface**
This repository works with video files at that are extremely large. As a lab, we store processed files in [huggingface.co](hf.co) buckets. Data that we have processed for this repo lands at https://huggingface.co/buckets/collective-logic-lab/honey-bee. Note that this repository contains a data/ directory with a few empty (and `.gitkeep`-ed) folders. A distribution script pulls some example data (including anything needed to run supplied notebooks) from the hf bucket to the development environment:

Run this from the repository root to set up the video tools:

```bash
cd hive_video
uv venv
source .venv/bin/activate
uv sync
uv run python get_dist_1.py
```