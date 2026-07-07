import threading

from keri_serviceaid.providers.artifact_store import LocalArtifactStore, FirstSeenResult


def test_first_publisher_is_first_seen():
    store = LocalArtifactStore()
    r = store.store("ESaid", b'{"$id":"ESaid"}', by="EAlice")
    assert isinstance(r, FirstSeenResult)
    assert r.first_seen is True
    assert r.first_publisher == "EAlice"
    assert store.get("ESaid") == b'{"$id":"ESaid"}'


def test_second_publisher_is_not_first_and_reports_prior():
    store = LocalArtifactStore()
    store.store("ESaid", b'{"$id":"ESaid"}', by="EAlice")
    r = store.store("ESaid", b'{"$id":"ESaid"}', by="EBob")
    assert r.first_seen is False
    assert r.first_publisher == "EAlice"   # the prior contributor


def test_concurrent_claims_yield_exactly_one_first_seen():
    store = LocalArtifactStore()
    results = []
    barrier = threading.Barrier(8)

    def claim(aid):
        barrier.wait()
        results.append(store.store("ESaid", b"{}", by=aid))

    threads = [threading.Thread(target=claim, args=(f"E{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r.first_seen) == 1
