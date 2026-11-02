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
