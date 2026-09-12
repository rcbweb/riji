# Contributing to Riji

Thank you for your interest in contributing to **Riji**! Riji is an open-source tool built for academic researchers, microscopists, and computational biologists. We welcome contributions that improve reproducibility, expand support for microscopy file formats, add new statistical metrics, or improve user experience.

---

## How You Can Contribute

### 1. Reporting Bugs & Microscopy Format Issues
Microscopy formats (`.czi`, `.nd2`, multi-page `.tif`) frequently differ across microscope vendors, firmware versions, and laser setups. If you encounter an unreadable file or unexpected behavior:
- Open an issue on GitHub using the **Bug Report** template.
- Specify your operating system, Python version, and microscope modality (e.g. Zeiss LSM 880, Nikon A1R).
- If possible, provide metadata (e.g., pixel dimensions, channel count, objective magnification) or a minimal cropped sample file to help us reproduce the issue.

### 2. Suggesting Scientific Features
If you would like to see support for a new colocalization metric (e.g., Costes significance test, Li's ICQ), custom segmentation model, or specific statistical test:
- Open a GitHub issue describing the scientific rationale and citation for the method.

### 3. Code Contributions & Pull Requests

#### Development Setup
1. Fork the repository on GitHub and clone your fork locally:
   ```bash
   git clone https://github.com/<your-username>/riji.git
   cd riji
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .\.venv\Scripts\activate
   # macOS / Linux:
   source .venv/bin/activate
   ```
3. Install dependencies in editable mode:
   ```bash
   pip install -e .
   pip install pytest
   ```
4. Run tests:
   ```bash
   pytest tests/
   ```

#### Pull Request Guidelines
- **Maintain Reproducibility**: Analyses performed in Riji must be fully reproducible. Any new parameter must be logged in the generated run configuration or `Results.xlsx`.
- **Preserve Ground Truth Separation**: Cell morphology / segmentation should remain strictly decoupled from fluorescence intensity ranking (see `cell_viability/__init__.py`).
- **Style**: Follow PEP 8 guidelines and write clear, informative docstrings.
- **Commit Messages**: Write concise, descriptive commit messages summarizing the change.

---

## Community & Scientific Integrity

By participating in this project, you agree to foster an open, welcoming, and collaborative scientific environment.
