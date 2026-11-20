def pytest_configure(config):
    config.addinivalue_line("markers", "slow: runs > 20 s; skipped by make swtest, run by make swtest-all")
