import pytest
from eda_sandbox.files import FileIndex, FileLimitExceeded, UnsafeFile, sanitize_display_name


@pytest.fixture
def file_index(tmp_path, monkeypatch) -> FileIndex:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr("eda_sandbox.files.IMPORTS", workspace / "inputs")
    monkeypatch.setattr("eda_sandbox.files.SOURCES", workspace / "sources")
    monkeypatch.setattr("eda_sandbox.files.OUTPUTS", workspace / "outputs")
    return FileIndex()


@pytest.mark.parametrize("name", ["../escape.csv", "/absolute.csv", "a\\..\\escape.csv", "\x00bad.csv"])
def test_display_name_never_becomes_a_path(file_index: FileIndex, name: str) -> None:
    record = file_index.import_bytes("input", name, b"safe")

    assert record.display_name == sanitize_display_name(name)
    assert file_index.path_for(record.file_id).parent.name == "inputs"


def test_symlink_is_never_opened(file_index: FileIndex) -> None:
    outputs = file_index.category_path("output")
    link = outputs / "link"
    link.symlink_to("/etc/passwd")

    with pytest.raises(UnsafeFile):
        file_index.index_output(link)


def test_import_over_limit_is_removed(file_index: FileIndex) -> None:
    with pytest.raises(FileLimitExceeded):
        file_index.import_bytes("input", "large.bin", b"x" * (file_index.max_import_bytes + 1))

    assert list(file_index.category_path("input").iterdir()) == []


def test_output_count_is_bounded(file_index: FileIndex, monkeypatch) -> None:
    monkeypatch.setattr("eda_sandbox.files.MAX_OUTPUT_FILES", 1)
    outputs = file_index.category_path("output")
    (outputs / "one.txt").write_bytes(b"one")
    (outputs / "two.txt").write_bytes(b"two")

    with pytest.raises(FileLimitExceeded):
        file_index.scan_outputs()
