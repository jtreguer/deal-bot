from concurrent.futures import ThreadPoolExecutor

from conftest import make_spec

from deal_bot.store import Store


def test_extraction_cache_survives_concurrent_threads(tmp_path):
    store = Store(tmp_path / "db.sqlite")

    def roundtrip(i: int) -> int | None:
        store.put_extraction(f"h{i}", "m", make_spec(ram_gb=i))
        return store.get_extraction(f"h{i}").ram_gb

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(roundtrip, range(400))) == list(range(400))
