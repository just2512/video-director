# Timeline state workstream

Branch: `work/timeline-state`

Own timeline/editor state consistency:
- Multi-selection and cross-track drag must commit as one transaction.
- Undo/redo must restore the full affected state, including linked clips and selection.
- External revision changes must invalidate stale gestures and commands.
- Project switching must prevent late async responses from mutating the newly selected project.

Primary regression gate: `tests/test_editor_undo_redo.py`, `tests/test_film_studio_timeline_history_browser.py`, `tests/test_film_studio_pending_revision.py`, `tests/test_film_studio_external_watch_browser.py`, `tests/test_editor_command_project_switch_browser.py`, `tests/test_visual_overlay_timeline.py`, `tests/test_audio_clip_timeline.py`.

Avoid generation persistence files. Changes to shared revision contracts must be documented for the lifecycle workstream.
