from webarchiver.core import hashing


def test_text_and_bytes_hash_match():
    assert hashing.sha256_text("hi") == hashing.sha256_bytes(b"hi")


def test_file_hash(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    assert hashing.sha256_file(p) == hashing.sha256_bytes(b"data")
