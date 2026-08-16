import sys
from pathlib import Path

import pytest

# Allow `import keiraku` from the repo root without an install step.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def empty_dir(tmp_path):
    """A directory that is NOT a Keiraku source."""
    d = tmp_path / "not-a-source"
    d.mkdir()
    return d


@pytest.fixture
def source(tmp_path):
    """An initialised Keiraku source."""
    import keiraku

    d = tmp_path / "source"
    keiraku.init_source(d)
    return d
