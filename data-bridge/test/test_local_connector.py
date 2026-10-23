from collections.abc import Iterator
from pathlib import Path

import pytest

from connector.local import LocalConnector
from exceptions import (
    ConnectorFileExistsError,
    ConnectorFileNotFoundError,
    ConnectorOperationError,
    InvalidConnectorArgumentError,
)


def test_listing_scope(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "child.txt").write_bytes(b"child")
    (tmp_path / "top.txt").write_bytes(b"top")
    connector = LocalConnector(path=tmp_path)

    assert connector.list() == [
        {
            "path": "nested",
            "size": (tmp_path / "nested").stat().st_size,
            "type": "directory",
        },
        {"path": "top.txt", "size": 3, "type": "file"},
    ]
    assert connector.list(recursive=True) == [
        {"path": "nested/child.txt", "size": 5, "type": "file"},
        {"path": "top.txt", "size": 3, "type": "file"},
    ]


@pytest.mark.parametrize("recursive", [False, True])
def test_listing_symlinks_without_following(tmp_path: Path, recursive: bool) -> None:
    (tmp_path / "file").write_bytes(b"contents")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "empty").mkdir()
    (tmp_path / "nested" / "child-link").symlink_to("../file")
    (tmp_path / "file-link").symlink_to("file")
    (tmp_path / "directory-link").symlink_to("nested", target_is_directory=True)
    (tmp_path / "broken-link").symlink_to("missing")
    connector = LocalConnector(tmp_path)

    expected = [
        {"path": "broken-link", "size": len("missing"), "type": "symlink"},
        {"path": "directory-link", "size": len("nested"), "type": "symlink"},
        {"path": "file", "size": 8, "type": "file"},
        {"path": "file-link", "size": len("file"), "type": "symlink"},
    ]
    if recursive:
        expected.append(
            {"path": "nested/child-link", "size": len("../file"), "type": "symlink"}
        )
    else:
        expected.append(
            {
                "path": "nested",
                "size": (tmp_path / "nested").stat().st_size,
                "type": "directory",
            }
        )
    assert connector.list(recursive=recursive) == expected


def test_read_write_and_replace(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    connector = LocalConnector(tmp_path)
    connector.write("nested/file.bin", b"\x00\xff\n")
    assert connector.read("nested/file.bin") == b"\x00\xff\n"
    connector.write("nested/file.bin", b"replacement", mode="replace")
    assert (tmp_path / "nested" / "file.bin").read_bytes() == b"replacement"


def test_create_never_overwrites_existing_entries(tmp_path: Path) -> None:
    connector = LocalConnector(tmp_path)
    connector.write("file", b"original")
    (tmp_path / "link").symlink_to("missing")
    for path in ("file", "link"):
        with pytest.raises(ConnectorFileExistsError):
            connector.write(path, b"replacement")
    assert connector.read("file") == b"original"
    assert not (tmp_path / "missing").exists()


def test_replace_requires_existing_file_and_truncates(tmp_path: Path) -> None:
    connector = LocalConnector(tmp_path)
    with pytest.raises(ConnectorFileNotFoundError):
        connector.write("missing", b"data", mode="replace")
    assert not (tmp_path / "missing").exists()
    connector.write("file", b"long contents")
    connector.write("file", b"short", mode="replace")
    assert connector.read("file") == b"short"
    connector.write("file", b"", mode="replace")
    assert connector.read("file") == b""


@pytest.mark.parametrize(
    ("lines", "max_bytes", "expected"),
    [(1, 100, b"first\n"), (10, 8, b"first\nse"), (10, 100, b"first\nsecond\nlast")],
)
def test_head_limits(
    tmp_path: Path, lines: int, max_bytes: int, expected: bytes
) -> None:
    (tmp_path / "file").write_bytes(b"first\nsecond\nlast")
    assert LocalConnector(tmp_path).head("file", lines, max_bytes) == expected


@pytest.mark.parametrize("method", ["read", "write", "head"])
@pytest.mark.parametrize("path", ["", "../outside", "/outside", "bad\x00path"])
def test_invalid_paths(tmp_path: Path, method: str, path: str) -> None:
    connector = LocalConnector(tmp_path)
    arguments = (path, b"data") if method == "write" else (path,)
    with pytest.raises(InvalidConnectorArgumentError):
        getattr(connector, method)(*arguments)


def test_symlinks_do_not_escape_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (home / "link").symlink_to(outside)
    (home / "loop").symlink_to(home, target_is_directory=True)
    connector = LocalConnector(home)

    assert connector.list(recursive=True) == [
        {"path": "link", "size": len(str(outside).encode()), "type": "symlink"},
        {"path": "loop", "size": len(str(home).encode()), "type": "symlink"},
    ]
    for operation in (connector.read, connector.head):
        with pytest.raises(InvalidConnectorArgumentError):
            operation("link")
    with pytest.raises(InvalidConnectorArgumentError):
        connector.write("link", b"replacement")
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize("method", ["read", "head"])
def test_missing_files(tmp_path: Path, method: str) -> None:
    with pytest.raises(ConnectorFileNotFoundError):
        getattr(LocalConnector(tmp_path), method)("missing")


def test_write_requires_existing_parent(tmp_path: Path) -> None:
    with pytest.raises(ConnectorFileNotFoundError):
        LocalConnector(tmp_path).write("missing/file", b"data")


def test_directory_is_not_a_file(tmp_path: Path) -> None:
    with pytest.raises(ConnectorOperationError):
        LocalConnector(tmp_path).read(".")


@pytest.mark.parametrize(("lines", "max_bytes"), [(0, 10), (10, 0), (-1, 10), (1, -1)])
def test_invalid_head_limits(tmp_path: Path, lines: int, max_bytes: int) -> None:
    with pytest.raises(InvalidConnectorArgumentError):
        LocalConnector(tmp_path).head("file", lines, max_bytes)


def test_home_must_exist_and_be_directory(tmp_path: Path) -> None:
    with pytest.raises(ConnectorFileNotFoundError):
        LocalConnector(tmp_path / "missing")
    file = tmp_path / "file"
    file.write_bytes(b"")
    with pytest.raises(InvalidConnectorArgumentError):
        LocalConnector(file)


@pytest.mark.parametrize("path", ["", " \t", "bad\x00path"])
def test_invalid_home_path(path: str) -> None:
    with pytest.raises(InvalidConnectorArgumentError):
        LocalConnector(path)


def test_listing_errors_are_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector = LocalConnector(tmp_path)

    def denied(path: Path) -> Iterator[Path]:
        raise PermissionError("private host path")

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(ConnectorOperationError, match="Local file operation failed."):
        connector.list(recursive=True)
