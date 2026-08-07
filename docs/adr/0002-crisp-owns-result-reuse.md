# vasp-sop is result-cache-blind; crisp owns result reuse

vasp-sop never reads or writes the VASP result cache. Result reuse — caching
completed calculations and restoring them later — is a crisp capability
(`crisp cache put|has|fetch|query|status|rebuild`, wrapping the `vasp-cache`
library). crisp caches results when it fetches them and materializes cached
outputs back into the worktree before a batch cycle runs; vasp-sop sees
results only as files on disk (a converged `OUTCAR`/`CONTCAR`/`vasprun.xml`
like any fresh calculation).

We chose this over "vasp-sop calls vasp-cache directly" because the boundary
is the execution layer: crisp already owns job submission, result transfer,
and cluster state, so it is the natural owner of the result store. The former
coupling required vasp-sop to import `vasp-cache` (not on PyPI, so the
package could not install from index alone), duplicated the cache surface as
a `vasp-sop cache` CLI and `CacheWorker`, and split the restore/write logic
across `core/cache.py` and the orchestrator.

Costs: until crisp implements auto-cache-on-fetch and materialization, vasp-sop
never bounces off cached results — it recomputes (correct, just slower). Any
result a future run could reuse must be present in the worktree for vasp-sop
to see it; the CPD → `unitcell/structure_opt` handoff (`handoff_target_results`)
now copies the canonical target set directly instead of a cache round-trip and
fails loudly if the set is incomplete.

vasp-sop retains its own local path roots (`core/paths.py`: `SOP_ROOT`,
MP caches, JobStore DB), which are not the result cache.