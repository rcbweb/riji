<div align="center">

# Riji

### Desktop Colocalization and Live-Cell Image Analysis for Confocal Microscopy

[![Release](https://img.shields.io/github/v/release/rcbweb/riji?color=blue&label=Release)](https://github.com/rcbweb/riji/releases)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)](https://github.com/rcbweb/riji/releases)
[![Citation](https://img.shields.io/badge/cite-CITATION.cff-orange.svg)](CITATION.cff)
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.placeholder-blue.svg)](https://zenodo.org/)

<p align="center">
  <b>A reproducible, high-throughput desktop application and computational pipeline for quantitative fluorescence microscopy, multi-channel colocalization, and morphology-based live-cell dye uptake quantification.</b>
</p>

[Quick Start](#-quick-start) •
[Scientific Highlights](#-scientific-highlights) •
[Data Layout](#-input-data-organization) •
[Outputs & Reproducibility](#-outputs--reproducibility) •
[CLI & Python API](#-command-line--scriptable-api) •
[Citation](#-citation)

</div>

---

## 🔬 Scientific Highlights

Quantitative fluorescence microscopy is frequently hindered by operator subjectivity in thresholding, complex multi-step ImageJ macro chains, and a critical artefact in live-cell imaging: **dead or compromised cells preferentially accumulate dye**, leading naive intensity-based segmentation to select dead cells as "high-uptake" false positives.

**Riji** addresses these challenges through a unified, reproducible pipeline:

- **Decoupled Morphology & Intensity**: Cell boundaries are determined strictly from brightfield morphology using **Cellpose** deep-learning models, shape descriptors, refractility, and isolation metrics. Brightness *never* decides what counts as a cell—ensuring dead, hyper-permeabilized cells are filtered out prior to quantification.
- **Robust Multi-Channel Colocalization**: Calculates Pearson Correlation Coefficients ($r$) and Manders Overlap Coefficients ($M_1, M_2$) across arbitrary dye combinations, measuring colocalization both within single segmented cells and across whole fields of view.
- **Human-in-the-Loop Active Learning**: An interactive visual inspection tool allows researchers to verify segmented cells, draw missed cells, reject debris, and retrain the selection model with persistent corrections.
- **Zero-Friction Publication Readiness**: Automatically outputs vector figures (`.pdf`, `.png`), multi-sheet Excel statistical workbooks (`Results.xlsx`) containing both per-cell and per-image replicated statistics, and an auto-generated `methods.txt` paragraph with your exact numerical parameters ready for manuscript submission.
- **Native Microscopy Format Support**: Direct parsing of Zeiss (`.czi`), Nikon (`.nd2`), and multi-page OME-TIFF (`.tif`, `.tiff`) stacks without requiring manual format conversion.

---

## 🚀 Quick Start

### Option A: Experimentalists & Wet-Lab Researchers (No Coding Required)

Pre-built, standalone desktop packages are available from the [**GitHub Releases**](https://github.com/rcbweb/riji/releases/latest) page:

#### 🪟 Windows
1. Download `Riji_Windows.zip` from [Releases](https://github.com/rcbweb/riji/releases/latest).
2. Right-click $\rightarrow$ **Extract All**.
3. Open the extracted folder and double-click `Riji.exe`.
   > *Note: Windows SmartScreen may show a one-time notice because the binary is unsigned. Click **More info** $\rightarrow$ **Run anyway** (see [SECURITY.md](SECURITY.md)).*

#### 🍎 macOS
1. Download `Riji-Mac.zip` from [Releases](https://github.com/rcbweb/riji/releases/latest) and extract it to your Desktop.
2. Open Terminal (`Cmd + Space`, type `Terminal`, hit `Enter`).
3. Paste the following line and press Enter:
   ```bash
   cd ~/Desktop/Riji-Mac && bash install.command
   ```
4. Once completed, launch Riji by double-clicking `Riji.app`.

---

### Option B: Computational Biologists & Developers (Source Install)

For developers, cluster environments, or Linux workstations:

#### Using Conda / Mamba (Recommended for Labs)
```bash
# Clone the repository
git clone https://github.com/rcbweb/riji.git
cd riji

# Create environment and install dependencies
conda env create -f environment.yml
conda activate riji

# Launch the desktop GUI
riji
# or: python run_riji.py
```

#### Using Standard Python & Pip
```bash
git clone https://github.com/rcbweb/riji.git
cd riji

# Create virtual environment (Python 3.10+ recommended)
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# Install in editable mode
pip install --upgrade pip
pip install -e .

# Launch Riji
riji
```

---

## 📁 Input Data Organization

Riji follows a structured, reproducible experiment layout. Group your raw microscopy images into condition subfolders:

```text
My_Experiment/                         <-- Select this folder in Riji
├── Control/
│   ├── WellA01_FOV1.czi (or .nd2, .tif)
│   └── WellA01_FOV2.czi
├── Treatment_5uM/
│   ├── WellB01_FOV1.czi
│   └── WellB01_FOV2.czi
└── Treatment_20uM/
    ├── WellC01_FOV1.czi
    └── WellC01_FOV2.czi
```

- Each **subfolder** represents an experimental condition or treatment group.
- Each image within a folder is treated as a biological/technical replicate.
- All results land neatly in an auto-created `coloc_results/` directory inside your experiment folder.

---

## 📊 Outputs & Reproducibility

Every run generates complete, publication-ready documentation:

```text
coloc_results/
├── Figures and Tables/
│   ├── uptake_figure.pdf          # Vector publication figure
│   ├── uptake_figure.png          # High-resolution raster figure
│   ├── Results.xlsx               # Raw measurements, Cell list, and Statistics
│   └── methods.txt                # Manuscript-ready methods paragraph
├── Cell Images/
│   ├── Control/                   # Montages & segmented cell overlays
│   │   ├── montage.png
│   │   └── overlays/ (Green = measured, Red = rejected, Blue = drawn)
│   └── Treatment_5uM/
└── README.txt                     # Execution log with exact parameters used
```

### Self-Documenting Methods Paragraph
Riji dynamically generates a `methods.txt` file summarizing image acquisition parameters, segmentation thresholds, cell counts, and statistical tests:
> *"Confocal fluorescence images were acquired across 3 conditions (N=24 replicates). Cellular boundaries were segmented using Cellpose deep learning on brightfield channels independently of fluorescence intensity... Colocalization was quantified via Pearson correlation ($r$) and Manders coefficients ($M_1, M_2$)..."*

---

## 💻 Command-Line & Scriptable API

In addition to the interactive GUI, Riji provides command-line interfaces for high-throughput cluster execution:

```bash
# Run multi-dye colocalization on an experiment folder
riji-coloc "path/to/My_Experiment"

# Check available dye channels in raw image metadata
riji-coloc --dyes "path/to/My_Experiment"

# Select and quantify the top 20 brightest live cells per folder
python -m riji.cell_viability.rank_top "path/to/My_Experiment" --top=20
```

---

## 🛡️ Security & Integrity

Riji is fully open-source scientific software. It:
- Does **not** transmit telemetry or contact external servers during analysis.
- Operates entirely locally on your machine.
- Leaves system Python and global operating system directories unmodified.
- For details regarding Apple Gatekeeper and Windows SmartScreen alerts on unsigned binaries, see [SECURITY.md](SECURITY.md).

---

## 📝 Citation

If you use Riji in academic research, published papers, or conference proceedings, please cite:

```bibtex
@software{bari2026riji,
  author       = {Bari, Rchin},
  title        = {Riji: Desktop Colocalization and Live-Cell Image Analysis for Confocal Microscopy},
  year         = {2026},
  version      = {1.0.0},
  publisher    = {GitHub},
  url          = {https://github.com/rcbweb/riji}
}
```

You can also use GitHub's **"Cite this repository"** button in the sidebar to export APA or BibTeX citations directly via [CITATION.cff](CITATION.cff).

---

## 📄 License

This project is licensed under the [MIT License](LICENSE) — free for both academic and commercial reuse with attribution.
