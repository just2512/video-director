"""Concurrency contract for single-use AI FilmDocument proposals."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from video_director.film_proposals import FilmProposalStore


def test_take_is_atomic_for_concurrent_duplicate_confirmations():
    store = FilmProposalStore()
    store.put("proposal-once", "project-a", "revision-1", [{"op": "remove", "clip": "clip-a"}])
    contenders = 12
    barrier = Barrier(contenders)

    def confirm(_index):
        barrier.wait(timeout=5)
        return store.take("project-a", "proposal-once", "revision-1")

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        results = list(pool.map(confirm, range(contenders)))

    accepted = [result for result in results if result is not None]
    rejected = [result for result in results if result is None]
    assert len(accepted) == 1
    assert len(rejected) == contenders - 1
    assert accepted[0]["operations"] == [{"op": "remove", "clip": "clip-a"}]
    assert store.take("project-a", "proposal-once", "revision-1") is None


def test_wrong_project_or_revision_does_not_consume_proposal():
    store = FilmProposalStore()
    store.put("proposal-scope", "project-a", "revision-1", [{"op": "remove", "clip": "clip-a"}])

    assert store.take("project-b", "proposal-scope", "revision-1") is None
    assert store.take("project-a", "proposal-scope", "revision-2") is None
    accepted = store.take("project-a", "proposal-scope", "revision-1")
    assert accepted is not None
    assert accepted["project_id"] == "project-a"
    assert accepted["base_revision"] == "revision-1"


def test_expired_proposal_is_rejected_and_pruned_without_wall_clock_sleep():
    now = [100.0]
    store = FilmProposalStore(ttl_seconds=15.0, clock=lambda: now[0])
    store.put("proposal-expiry", "project-a", "revision-1", [{"op": "remove", "clip": "clip-a"}])

    now[0] = 114.999
    assert store.take("project-a", "proposal-expiry", "revision-1") is not None

    # Use exactly representable integer timestamps for the TTL boundary; the
    # decimal pair 114.999/129.999 can subtract to slightly less than 15.0.
    now[0] = 115.0
    store.put("proposal-expiry-2", "project-a", "revision-1", [{"op": "remove", "clip": "clip-b"}])
    now[0] = 130.0
    assert store.take("project-a", "proposal-expiry-2", "revision-1") is None


def test_proposal_payload_is_defensively_copied_on_insert_and_consume():
    store = FilmProposalStore()
    operations = [{"op": "remove", "clip": "clip-a", "metadata": {"reason": "user"}}]
    store.put("proposal-copy", "project-a", "revision-1", operations)
    operations[0]["metadata"]["reason"] = "mutated-after-put"

    consumed = store.take("project-a", "proposal-copy", "revision-1")
    assert consumed is not None
    assert consumed["operations"][0]["metadata"]["reason"] == "user"
    consumed["operations"][0]["metadata"]["reason"] = "mutated-after-take"
    assert store.take("project-a", "proposal-copy", "revision-1") is None


def test_invalid_proposal_payload_is_rejected_before_store_mutation():
    import pytest

    store = FilmProposalStore()
    invalid = [
        ("", "project-a", "revision-1", [{"op": "remove"}]),
        ("proposal", "", "revision-1", [{"op": "remove"}]),
        ("proposal", "project-a", "", [{"op": "remove"}]),
        ("proposal", "project-a", "revision-1", []),
        ("proposal", "project-a", "revision-1", None),
    ]
    for proposal_id, project_id, revision, operations in invalid:
        with pytest.raises(ValueError):
            store.put(proposal_id, project_id, revision, operations)
    assert store.take("project-a", "proposal", "revision-1") is None

def test_get_keeps_proposal_retryable_until_successful_apply_discards_it():
    store = FilmProposalStore()
    operations = [{"op": "remove", "clip": "clip-a"}]
    store.put("proposal-retry", "project-a", "revision-1", operations)

    first = store.get("project-a", "proposal-retry", "revision-1")
    assert first is not None
    first["operations"].clear()
    assert store.get("project-a", "proposal-retry", "revision-1")["operations"] == operations

    # A failed document transaction does not call discard, so the same proposal
    # remains available for an explicit retry. Successful apply then consumes it.
    assert store.get("project-a", "proposal-retry", "revision-1") is not None
    assert store.discard("project-a", "proposal-retry") is True
    assert store.get("project-a", "proposal-retry", "revision-1") is None
    assert store.discard("project-a", "proposal-retry") is False
