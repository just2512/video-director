# Project lifecycle E2E workstream

Branch: `work/lifecycle-e2e`

Own end-to-end acceptance of project lifecycle:
- Create a project and initialize its canonical film document.
- Migrate an existing project without losing assets or timeline semantics.
- Edit, persist, reopen, export, and run QC against one consistent project revision.
- Assert failure recovery and artifact provenance; never substitute mocked results for real environment acceptance.

Primary regression gate: `tests/test_project_manager.py`, `tests/test_project_manager_film_authority.py`, `tests/test_film_full_pipeline_e2e.py`, `tests/test_film_studio_migration_preview_browser.py`, `tests/test_film_render_publication_transaction.py`, `tests/test_film_render_scene_e2e.py`.

Prefer tests and orchestration changes. Coordinate with generation and timeline branches before changing shared contracts.
