import threading

from cache import InMemoryCache, cache_key


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_cache_key_is_stable_across_keyword_order():
    first = cache_key("meetings", "get", 7, {"meeting_id": 3, "status": "pending"})
    second = cache_key("meetings", "get", 7, {"status": "pending", "meeting_id": 3})

    assert first == second
    assert first.startswith("meetings.get|7|")
    assert cache_key("meetings", "get", 7, {"meeting_id": 3}) == 'meetings.get|7|{"meeting_id": 3}'


def test_get_missing_is_none_and_set_then_get_returns_value():
    cache = InMemoryCache()

    assert cache.get("alpha.get|7|{}") is None
    cache.set("alpha.get|7|{}", {"id": 1})
    assert cache.get("alpha.get|7|{}") == {"id": 1}


def test_clear_removes_only_the_service_prefix_for_every_user():
    cache = InMemoryCache()
    for key in ("alpha.get|7|{}", "alpha.list|8|{}", "alphabet.get|7|{}", "beta.get|7|{}"):
        cache.set(key, key)

    cache.clear("alpha")

    assert cache.get("alpha.get|7|{}") is None
    assert cache.get("alpha.list|8|{}") is None
    assert cache.get("alphabet.get|7|{}") == "alphabet.get|7|{}"
    assert cache.get("beta.get|7|{}") == "beta.get|7|{}"


def test_entry_expires_at_ttl_with_injected_clock():
    clock = FakeClock()
    cache = InMemoryCache(ttl_seconds=60, clock=clock)
    cache.set("alpha.get|7|{}", 1)

    clock.now = 59.9
    assert cache.get("alpha.get|7|{}") == 1

    clock.now = 60
    assert cache.get("alpha.get|7|{}") is None
    assert "alpha.get|7|{}" not in cache._entries

    clock.now = 61
    assert cache.get("alpha.get|7|{}") is None


def test_concurrent_set_and_get_from_threads():
    cache = InMemoryCache()
    failures: list[str] = []

    def worker(thread_id: int) -> None:
        for i in range(200):
            key = cache_key("alpha", "get", thread_id, {"i": i})
            cache.set(key, (thread_id, i))
            if cache.get(key) != (thread_id, i):
                failures.append(key)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    for thread_id in range(8):
        for i in range(200):
            assert cache.get(cache_key("alpha", "get", thread_id, {"i": i})) == (thread_id, i)
