# FilmDocument operation-interaction matrix

**Owner:** W2 timeline transaction state.  
**Purpose:** replace an unbounded “all permutations” expectation with a finite, risk-based interaction contract.  
**Rule:** operation-kind presence is not sufficient. Each listed interaction must preserve the document invariants below on the exact tested SHA.

## Operation inventory

The current `film_document.apply_ops()` dispatches these 20 operation kinds:

| Area | Operation kinds | Primary contract |
|---|---|---|
| Markers | `marker_add`, `marker_set`, `marker_remove` | Stable IDs, valid timeline bounds, canonical metadata serialization |
| Clip protection | `clip_lock` | Explicit boolean; locked clips reject direct and indirect mutation |
| HTML element | `element_set` | Only validated selected HTML targets and safe overrides |
| Clip attributes | `set` | Field whitelist, type/range validation, stable source ranges |
| Timeline placement | `move`, `insert`, `insert_ripple`, `remove`, `ripple_delete` | Correct track/time placement, overlap rules, ripple scope and stable split IDs |
| Source/timeline edits | `split`, `trim`, `slide`, `slip`, `roll`, `join` | Source-time continuity, duration/bounds, reversible edit semantics |
| Track structure | `track_add`, `track_remove`, `track` | Unique IDs, non-empty/last-track protection, track flags and lock enforcement |

## Invariants for every multi-operation batch

1. **Atomicity:** if any operation fails, the input document and persisted `film.html` remain unchanged.
2. **Revision:** a successful batch produces one authoritative revision, not one revision per operation.
3. **Canonical round trip:** `parse_film(serialize_film(doc))` preserves the document's semantic state and canonical timeline order.
4. **History:** one batch is one undo/redo unit; undo restores the full prior state and redo restores the full committed state.
5. **Lock propagation:** a locked clip or track cannot be changed directly or indirectly by another operation in the same batch.
6. **Stable identity:** split-generated IDs, clip IDs, track IDs and marker IDs remain deterministic and unambiguous.
7. **Time/source consistency:** operations that change source ranges preserve the intended source-to-timeline mapping and all dependent fade, volume-envelope and keyframe windows.
8. **Revision isolation:** stale client revisions and project switches cannot commit a batch against a different FilmDocument.

## Finite interaction cases

| Interaction | Required assertion | Existing evidence / state |
|---|---|---|
| `marker_add → marker_set` | One revision; final marker canonical; undo/redo restores marker state | `test_film_markers_commit_as_one_revision_and_share_undo_redo_history` |
| `marker_add → set(clip)` | Marker and clip attribute commit in one revision and one history entry | New W2 regression in `test_film_timeline_markers.py`; exact-SHA CI pending |
| `marker_add → invalid set(clip)` | Whole batch rejects; no marker leaks into input state | New W2 atomicity regression; exact-SHA CI pending |
| `track_add → insert` | New track and clip are committed together with stable IDs | `test_new_track_and_insert_are_one_stable_id_operation_batch` |
| `track_add → track_remove` | Temporary empty track leaves no semantic residue; canonical round trip remains equal | New W2 regression in `test_film_document_contract.py`; exact-SHA CI pending |
| `clip_lock(false) → set` | Explicit unlock permits the following edit in the same batch | `test_clip_lock_can_be_explicitly_removed_then_normal_edits_resume` |
| locked clip → `set/move/remove/split/trim/slip` | Each direct mutation is rejected | Parameterized `test_clip_level_lock_rejects_direct_timeline_mutations` |
| `ripple_delete` affecting locked clip | Indirect shift is rejected and input remains unchanged | `test_ripple_delete_refuses_to_shift_a_locked_clip_and_keeps_input_unchanged` |
| `split → join` | Original source range is restored and history remains undoable | `test_join_through_edit_restores_split_source_range_and_preserves_undoable_document` |
| `split/trim/ripple → fade` | Fade windows remain on retained outer edges; no restart or invalid cut | Fade partition regressions in `test_film_document_contract.py` |
| `split/trim/ripple → volume_envelope` | Envelope points are sliced/rebased without changing the audible curve | `test_volume_envelope_is_sliced_and_rebased_by_split_trim_and_ripple` |
| `split/trim/roll/slide → data-film-keyframes` | Keyframe times remain clip-local, bounded and curve-preserving | Visual-keyframe rebase and bounds regressions in `test_film_document_contract.py` |
| cross-track `move` → overlap validation | Invalid overlap rejects before mutating source or target tracks | `test_cross_track_move_overlap_is_rejected_before_mutating_original_document` |
| `insert_ripple → ripple_delete` | Split IDs and retained-edge fade semantics remain deterministic | `test_ripple_delete_closes_gap_across_tracks_and_replays_identical_split_ids` and fade partition regression |

This is a deliberately finite set of coupled contracts, not a claim that every possible ordered pair among 20 operation kinds has been enumerated. Add a case only when two operations share state, invariants, serialization, history, or lock behavior.

## Exact-SHA acceptance

The new W2 tests are not PASS merely because they were committed. The lane must pass on the exact code SHA containing both regressions. After any subsequent code/workflow change, rerun the affected W2 job and integrated release gates. Do not weaken assertions, skip cases, or infer current evidence from older SHAs.
