"""Archive access tests.

ODB++ reaches a parser in three shapes: an exploded directory, a tarball and a
zip; any individual file may additionally be gzip-compressed in place.  Entity
names are case-insensitive because the matrix writes ``F.CU`` where the directory
is ``f.cu``, and that mismatch only breaks on case-sensitive filesystems.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from pcbkit.archive import OdbArchive, read_maybe_gzipped
from pcbkit.errors import ArchiveError
from pcbkit.diagnostics import CASE_INSENSITIVE_COLLISION, Diagnostics

from conftest import write_tree

JOB = {
    "odb/misc/info": "UNITS=MM\nJOB_NAME=probe\n",
    "odb/matrix/matrix": "STEP {\n NAME=pcb\n}\n",
    "odb/steps/pcb/stephdr": "UNITS=MM\n",
    "odb/steps/pcb/layers/f.cu/features": "UNITS=MM\nF 0\n",
}


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    return write_tree(tmp_path / "job", JOB)


class TestDirectorySource:
    def test_reads_the_job_root(self, job_dir):
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.require_job_root() == "odb"

    def test_is_dir_and_exists(self, job_dir):
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.is_dir("odb/steps")
            assert not archive.is_dir("odb/steps/pcb/stephdr")
            assert archive.exists("odb/misc/info")
            assert not archive.exists("odb/misc/nope")

    def test_list_dir_returns_immediate_children_only(self, job_dir):
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.list_dir("odb") == ["matrix", "misc", "steps"]

    def test_read_text_strips_a_leading_bom(self, job_dir):
        (job_dir / "odb" / "bom.txt").write_bytes(b"\xef\xbb\xbfA=1\n")
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.read_text("odb/bom.txt") == "A=1\n"

    def test_read_text_leaves_line_endings_alone(self, job_dir):
        # Normalisation happens in textio.iter_content_lines, not here: raw bytes
        # are the only thing some callers (checksum sidecars) care about.
        (job_dir / "odb" / "crlf.txt").write_bytes(b"A=1\r\nB=2\r\n")
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.read_text("odb/crlf.txt") == "A=1\r\nB=2\r\n"


class TestCaseInsensitiveLookup:
    def test_the_matrix_name_finds_the_lowercase_directory(self, job_dir):
        # research 03 R1: matrix F.CU vs directory f.cu.
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.resolve("odb/steps/pcb/layers/F.CU/features") \
                == "odb/steps/pcb/layers/f.cu/features"
            assert archive.exists("ODB/MISC/INFO")

    def test_two_members_differing_only_in_case_are_reported(self, tmp_path):
        # Built as a tar because a case-insensitive filesystem (Windows, macOS)
        # cannot hold both names as real files.
        archive_path = tmp_path / "collide.tar"
        with tarfile.open(archive_path, "w") as tar:
            for name in ("odb/misc/generated", "odb/misc/GENERATED"):
                payload = b"UNITS=MM\n"
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        diagnostics = Diagnostics()
        with OdbArchive.open(str(archive_path), diagnostics=diagnostics) as archive:
            codes = {d.code for d in diagnostics.items}
            # The first member wins, deterministically.
            assert archive.resolve("odb/misc/generated") == "odb/misc/generated"
        assert CASE_INSENSITIVE_COLLISION in codes

    def test_an_unknown_member_raises(self, job_dir):
        with OdbArchive.open(str(job_dir)) as archive:
            with pytest.raises(ArchiveError):
                archive.read_bytes("odb/nope")


class TestTarSource:
    def test_tgz(self, job_dir, tmp_path):
        archive_path = tmp_path / "job.tgz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(job_dir / "odb", arcname="odb")
        with OdbArchive.open(str(archive_path)) as archive:
            assert archive.require_job_root() == "odb"
            assert archive.is_dir("odb/steps/pcb")
            assert "UNITS=MM" in archive.read_text("odb/misc/info")

    def test_a_tarball_with_a_single_wrapping_directory(self, job_dir, tmp_path):
        archive_path = tmp_path / "wrapped.tgz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(job_dir / "odb", arcname="release/odb")
        with OdbArchive.open(str(archive_path)) as archive:
            assert archive.require_job_root() == "release/odb"

    def test_uncompressed_tar(self, job_dir, tmp_path):
        archive_path = tmp_path / "job.tar"
        with tarfile.open(archive_path, "w") as tar:
            tar.add(job_dir / "odb", arcname="odb")
        with OdbArchive.open(str(archive_path)) as archive:
            assert archive.require_job_root() == "odb"


class TestZipSource:
    def test_zip(self, job_dir, tmp_path):
        archive_path = tmp_path / "job.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            for path in sorted(job_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(job_dir).as_posix())
        with OdbArchive.open(str(archive_path)) as archive:
            assert archive.require_job_root() == "odb"
            assert archive.list_dir("odb") == ["matrix", "misc", "steps"]


class TestJobRootDiscovery:
    def test_multiple_candidates_are_reported(self, tmp_path):
        root = write_tree(tmp_path / "job", {
            "a/misc/info": "UNITS=MM\n",
            "a/matrix/matrix": "STEP {\n NAME=pcb\n}\n",
            "b/misc/info": "UNITS=MM\n",
            "b/matrix/matrix": "STEP {\n NAME=pcb\n}\n",
        })
        diagnostics = Diagnostics()
        with OdbArchive.open(str(root), diagnostics=diagnostics) as archive:
            assert archive.find_job_roots() == ["a", "b"]
            assert archive.require_job_root() == "a"
        assert any(d.code == "archive.multiple_job_roots" for d in diagnostics.items)

    def test_no_candidate_at_all(self, tmp_path):
        root = write_tree(tmp_path / "job", {"readme.txt": "hello\n"})
        with OdbArchive.open(str(root)) as archive:
            with pytest.raises(ArchiveError):
                archive.require_job_root()


class TestGzippedMembers:
    def test_read_maybe_gzipped_passes_plain_bytes_through(self):
        assert read_maybe_gzipped(b"UNITS=MM\n") == b"UNITS=MM\n"

    def test_read_maybe_gzipped_inflates_by_magic_bytes_not_by_name(self):
        # research 03 R9: compression is detected by the magic bytes, because a
        # compressed member keeps its uncompressed-looking name.
        payload = gzip.compress(b"UNITS=MM\nF 2\n")
        assert read_maybe_gzipped(payload) == b"UNITS=MM\nF 2\n"

    def test_a_file_named_features_can_still_be_a_plain_text_file(self, tmp_path):
        root = write_tree(tmp_path / "job", JOB)
        (root / "odb" / "steps" / "pcb" / "layers" / "f.cu" / "features").write_bytes(
            b"UNITS=MM\nF 0\n")
        with OdbArchive.open(str(root)) as archive:
            assert archive.read_text("odb/steps/pcb/layers/f.cu/features").endswith("F 0\n")

    def test_a_gzip_member_is_transparently_readable(self, tmp_path):
        root = write_tree(tmp_path / "job", JOB)
        target = root / "odb" / "steps" / "pcb" / "layers" / "f.cu" / "features"
        target.write_bytes(gzip.compress(b"UNITS=MM\nF 7\n"))
        with OdbArchive.open(str(root)) as archive:
            assert "F 7" in archive.read_text("odb/steps/pcb/layers/f.cu/features")


class TestBadInput:
    def test_a_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(ArchiveError):
            OdbArchive.open(str(tmp_path / "missing"))

    def test_a_corrupt_zip_is_reported_not_crashed(self, tmp_path):
        path = tmp_path / "broken.zip"
        path.write_bytes(b"PK\x03\x04rubbish")
        with pytest.raises(ArchiveError):
            OdbArchive.open(str(path))

    def test_an_unknown_binary_format_is_rejected(self, tmp_path):
        path = tmp_path / "thing.bin"
        path.write_bytes(b"\x00\x01\x02not an archive")
        with pytest.raises(ArchiveError):
            OdbArchive.open(str(path))

    def test_text_decoding_failure_is_tolerated(self, job_dir):
        # ODB++ is ASCII by spec, but a stray high byte must not make a whole
        # board unreadable.
        (job_dir / "odb" / "bad").write_bytes(b"UNITS=MM\n\xff\xfe\n")
        with OdbArchive.open(str(job_dir)) as archive:
            text = archive.read_text("odb/bad")
        assert text.startswith("UNITS=MM")

    def test_a_leading_bom_does_not_become_part_of_the_first_token(self, job_dir):
        (job_dir / "odb" / "bom").write_bytes(b"\xef\xbb\xbfUNITS=MM\n")
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.read_text("odb/bom") == "UNITS=MM\n"


class TestWindowsPaths:
    def test_backslashes_and_leading_dot_slash_are_normalised(self, job_dir):
        with OdbArchive.open(str(job_dir)) as archive:
            assert archive.exists(".\\odb\\misc\\info")
            assert archive.exists("./odb/misc/info")
            assert archive.exists("odb//misc//info")
