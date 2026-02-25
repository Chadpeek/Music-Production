# Contributing to Producer OS

Thank you for your interest in contributing to Producer OS! We welcome
contributions from users and developers of all experience levels. Before
submitting an issue or pull request, please read the following guidelines to
help us maintain a friendly and productive community.

## Getting Started

1. **Install dependencies**: This project uses Python ≥3.9. We recommend
   creating a virtual environment and installing dependencies with pip:

   ```bash
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[dev]"
   python -m pytest -q
   ```

2. **Run tests**: Before making changes, ensure the existing test suite
   passes:

   ```bash
   pytest
   ```

3. **Create a branch**: Use a descriptive branch name for your work:

   ```bash
   git checkout -b feature/my-improvement
   ```

## Development Guidelines

* **Follow the rules**: Producer OS has strict behaviour‑preserving rules
  around file organisation and styling. Do not change sorting logic or
  default bucket definitions without discussing with the maintainers.

* **Write tests**: New features should include tests under the `tests/`
  directory. Use pytest and fixtures when appropriate.

* **Style and typing**: We use `ruff` for linting and `mypy` for static
  type checking. Run these locally before submitting:

  ```bash
  ruff producer_os
  mypy producer_os
  ```

* **Sign your work**: Include a clear description of your changes in the
  commit messages and pull request. Explain why the change is needed.

## Reporting Issues

If you encounter a bug or have a feature request, please open an issue on
GitHub. Include as much detail as possible to help us reproduce the problem:

* Steps to reproduce
* Expected vs. actual behaviour
* Sample input files or configuration (if applicable)

We appreciate your feedback and will do our best to respond promptly.

## Code of Conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/)
code of conduct. By participating, you are expected to uphold this code.
Please report unacceptable behaviour to the maintainers.

## License

By contributing, you agree your contributions are licensed under the project’s license (GPL-3.0).