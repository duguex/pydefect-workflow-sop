---
name: vasp-sop-deps-audit-graph
description: "Use when designing, reviewing, or debugging the vasp-sop/crisp blocker-first dependency audit graph."
---

# Dependency-audit graph redesign

Use this when rebuilding or reviewing the vasp-sop `/deps` graph in crisp.

1. Treat a dependency graph as an audit model, not a directory tree. Classify every relation as exactly one of:
   - runtime gate: currently prevents execution;
   - data/structure lineage: explains an input/result source;
   - dispatch: branches the current orchestrator can dispatch in one cycle;
   - containment: ownership only.
2. Prove every runtime gate against `vasp_sop/core/orchestrator.py` or `core/system.py`. Do not draw phase cursor or containment as a runtime gate.
3. Default to blocker-first UI:
   - blocking roots have no unmet runtime-gate ancestor and at least one blocked descendant;
   - partition roots manual → automatic → wait;
   - sort within each partition by transitive affected downstream count;
   - select a root to show only its causal runtime-gate subgraph;
   - search a task to trace upstream instead.
4. Aggregate only by established domain objects: a defect chain can hide/show charge states; do not aggregate unrelated nodes merely because they share a status.
5. Render graph layers with visibly distinct encodings. Keep hard gates on by default; make lineage/dispatch/containment optional.
6. Mirror actual orchestrator disposition rules, including CPD/defect retry asymmetry. Surface that asymmetry; never normalize it in presentation.
7. Keep raw JSON for the API/CLI, not the WebUI.
8. Test: source graph contracts (no false `structure_opt` fan-in, real analysis gates, seed gate + lineage), frontend blocker-first selection/layer controls, live API payload, and browser graph canvas height.
