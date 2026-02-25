name: Bug report
description: Report a reproducible problem in Producer-OS
title: "[Bug]: "
labels: ["bug"]

body:
  - type: markdown
    attributes:
      value: |
        Thanks for reporting a bug.
        Please provide enough detail so we can reproduce it safely.

  - type: input
    id: version
    attributes:
      label: Producer-OS version
      description: e.g. v0.1.0 or main @ commit hash
      placeholder: "v0.1.0 or main @ <commit>"
    validations:
      required: true

  - type: input
    id: python
    attributes:
      label: Python version
      placeholder: "3.11.x / 3.12.x"
    validations:
      required: true

  - type: dropdown
    id: os
    attributes:
      label: Operating system
      options:
        - Windows
        - macOS
        - Linux
        - Other
    validations:
      required: true

  - type: dropdown
    id: mode
    attributes:
      label: Usage mode
      options:
        - CLI
        - GUI
        - Both
    validations:
      required: true

  - type: textarea
    id: what-happened
    attributes:
      label: What happened?
      placeholder: "When I run ..., I expected ..., but ..."
    validations:
      required: true

  - type: textarea
    id: steps
    attributes:
      label: Steps to reproduce
      description: Provide minimal steps and example inputs (no private files).
      placeholder: |
        1. ...
        2. ...
        3. ...
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: Expected behavior
      placeholder: "I expected ..."
    validations:
      required: true

  - type: textarea
    id: logs
    attributes:
      label: Logs / output
      description: Paste relevant console output (redact personal paths if needed).
      render: text
    validations:
      required: false

  - type: checkboxes
    id: confirmations
    attributes:
      label: Confirmations
      options:
        - label: I searched existing issues.
          required: true
        - label: I can reproduce this consistently.
          required: true
        - label: I am not sharing private audio or personal data.
          required: true