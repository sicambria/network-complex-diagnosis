from netdiag import percentile, series_stats, jitter_ms, clean_float


class TestPercentile:
    def test_empty(self):
        assert percentile([], 50) is None

    def test_single(self):
        assert percentile([42], 50) == 42
        assert percentile([42], 95) == 42
        assert percentile([42], 99) == 42

    def test_p50_odd(self):
        assert percentile([1, 2, 3], 50) == 2

    def test_p50_even(self):
        values = [1, 2, 3, 4]
        p = percentile(values, 50)
        assert 2.0 <= p <= 3.0

    def test_p95(self):
        values = list(range(1, 101))
        p = percentile(values, 95)
        assert 95.0 <= p <= 96.0

    def test_p99(self):
        values = list(range(1, 101))
        p = percentile(values, 99)
        assert 99.0 <= p <= 100.0


class TestCleanFloat:
    def test_none(self):
        assert clean_float(None) is None

    def test_rounding(self):
        assert clean_float(3.14159) == 3.14
        assert clean_float(10.0) == 10.0
        assert clean_float(0.001) == 0.0
        assert clean_float(1.999) == 2.0

    def test_int_input(self):
        assert clean_float(5) == 5.0


class TestSeriesStats:
    def test_empty(self):
        s = series_stats([])
        assert s["count"] == 0
        assert s["min_ms"] is None
        assert s["avg_ms"] is None

    def test_single_value(self):
        s = series_stats([10])
        assert s["count"] == 1
        assert s["min_ms"] == 10.0
        assert s["avg_ms"] == 10.0
        assert s["max_ms"] == 10.0
        assert s["p50_ms"] == 10.0
        assert s["stdev_ms"] == 0

    def test_multi(self):
        values = [10, 20, 30, 40, 50]
        s = series_stats(values)
        assert s["count"] == 5
        assert s["min_ms"] == 10.0
        assert s["max_ms"] == 50.0
        assert s["avg_ms"] == 30.0
        assert s["p50_ms"] == 30.0
        assert s["stdev_ms"] > 0

    def test_non_integer(self):
        s = series_stats([1.5, 2.5, 3.5])
        assert s["avg_ms"] == 2.5
        assert s["min_ms"] == 1.5


class TestJitterMs:
    def test_none_short(self):
        assert jitter_ms([]) is None
        assert jitter_ms([10]) is None

    def test_constant(self):
        assert jitter_ms([10, 10, 10]) == 0.0

    def test_varying(self):
        j = jitter_ms([10, 20, 10])
        assert j == 10.0
