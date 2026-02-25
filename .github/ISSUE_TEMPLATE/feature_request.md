name: Feature request
description: Suggest an improvement or new feature
title: "[Feature]: "
labels: ["enhancement"]

body:
  - type: markdown
    attributes:
      value: |
        Thanks for the idea!
        Producer-OS is safety-first, rule-driven, and configurable.
        Please describe the problem clearly before proposing a solution.

  - type: textarea
    id: problem
    attributes:
      label: Problem statement
      description: What problem are you trying to solve?
      placeholder: "Currently it is difficult to..."
    validations:
      required: true

  - type: textarea
    id: proposal
    attributes:
      label: Proposed solution
      description: What would you like Producer-OS to do?
      placeholder: "Add/Change ... so that ..."
    validations:
      required: true

  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
      description: Have you considered other approaches?
      placeholder: "Other approaches I considered..."
    validations:
      required: false

  - type: dropdown
    id: impact
    attributes:
      label: Expected impact
      description: Rough scope of change
      options:
        - Small (minor UX or small rule adjustment)
        - Medium (new option or bucket behavior)
        - Large (new subsystem or major behavior change)
    validations:
      required: true

  - type: checkboxes
    id: safety
    attributes:
      label: Safety & constraints
      options:
        - label: The feature should remain non-destructive by default (no deletes).
          required: true
        - label: The feature should log decisions clearly ("why" for actions).
          required: true
        - label: The feature should be configurable (JSON + schema).
          required: false