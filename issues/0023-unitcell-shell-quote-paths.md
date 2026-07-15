# Unitcell pydefect command leaves absolute paths unquoted

**Date:** 2026-07-15  
**Severity:** P1  
**Example:** `Sn(SeO3)2/unitcell`

## Symptom

`pydefect_vasp u` fails with `/bin/sh: Syntax error: "(" unexpected` although the formula is quoted. Absolute arguments `-vb`, `-ob`, `-odc`, `-odi` contain the parenthesized project directory and are not shell-quoted.

## Root cause

`build_unitcell_yaml()` interpolates `Path` values directly into a shell command. Only `-n` was quoted.

## Acceptance

- [ ] All path and formula arguments use `shlex.quote`
- [ ] Regression test under a project path containing parentheses
- [ ] `Sn(SeO3)2` can attempt `pydefect_vasp u` without shell syntax error
