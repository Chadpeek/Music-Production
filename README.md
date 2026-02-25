```markdown
<p align="center">
  <h1 align="center">Producer OS</h1>
  <p align="center">
    Structured sample management.
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue" />
  <img src="https://img.shields.io/badge/Build-Nuitka-purple" />
  <img src="https://img.shields.io/badge/License-MIT-green" />
</p>

---

## Overview

Producer OS is a structured system for organizing sample packs and production assets.

It transforms unstructured folders into a clean, repeatable hub layout — without destructive behavior.

Designed for long-term use.

---

## Core Principles

- Safe by default  
- Transparent in operation  
- Re-runnable without duplication  
- Strict separation of responsibilities  
- Logging-first architecture  

---

## What It Does

- Wraps loose files into pack folders  
- Routes content into defined buckets  
- Preserves vendor structure (optional)  
- Logs every action  
- Quarantines uncertain input  
- Avoids reprocessing organized packs  

---

## Output Structure

```

Hub/
├── Drum Kits/
├── Samples/
├── FL Projects/
├── MIDI Packs/
├── Presets/
├── UNSORTED/
└── Quarantine/

```

Clean. Predictable. Repeatable.

---

## Architecture

```

UI Layer        → User interaction
Engine          → Sorting logic
Services        → Config / Styles / Buckets
CLI             → Headless execution
Tests           → Validation

````

No combined responsibilities.

---

## Execution

Development:

```bash
pip install -r requirements.txt
python -m producer_os.producer_os_app
````

Build (Nuitka):

```bash
python -m nuitka --standalone --enable-plugin=pyside6 build_gui_entry.py
```

---

## Safety Model

* No deletion by default
* Low-confidence → `UNSORTED`
* Suspicious input → `Quarantine`
* All actions logged

---

## Re-Run Behavior

First run:

* Distributes content

Second run:

* Skips previously processed packs
* Prevents duplication

Designed for repeated execution.

---

## Roadmap

* Waveform-based classification
* BPM / key detection
* Rule editor in UI
* Advanced duplicate detection

---

Producer OS is not a script.

It is a structured production environment.

```

---

# 🎯 Why This Works Better

- Short sentences
- No fluff
- Clear hierarchy
- Minimal emotion
- Controlled tone
- White space
- Quiet confidence

It now feels like:
- A productivity tool
- A system
- Intentional software
- Not a hobby project

---
Say **“Ultra minimal”** if you want to push it even cleaner.
```
