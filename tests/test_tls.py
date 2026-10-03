"""Offline certificate-chain regressions; no sockets or SEI session access."""
from __future__ import annotations

from pathlib import Path
import re
import ssl
import subprocess

import pytest

from sei_cli import auth, tls


def _openssl(directory: Path, *args: str) -> None:
    subprocess.run(
        ["openssl", *args], cwd=directory, check=True,
        capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("offline-tls")
    for root in ("root", "unrelated"):
        _openssl(
            directory, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", f"{root}.key", "-out", f"{root}.pem", "-days", "2",
            "-subj", f"/CN=Offline {root}",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:1",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-addext", "subjectKeyIdentifier=hash",
        )
    _openssl(
        directory, "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "intermediate.key", "-out", "intermediate.csr",
        "-subj", "/CN=Offline intermediate",
    )
    (directory / "intermediate.ext").write_text(
        "basicConstraints=critical,CA:TRUE,pathlen:0\n"
        "keyUsage=critical,keyCertSign,cRLSign\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always,issuer\n",
        encoding="ascii",
    )
    _openssl(
        directory, "x509", "-req", "-in", "intermediate.csr",
        "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial",
        "-out", "intermediate.pem", "-days", "2", "-extfile", "intermediate.ext",
    )
    _openssl(
        directory, "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "server.key", "-out", "server.csr",
        "-subj", "/CN=sei.example.test",
    )
    (directory / "server.ext").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:sei.example.test\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always,issuer\n",
        encoding="ascii",
    )
    _openssl(
        directory, "x509", "-req", "-in", "server.csr",
        "-CA", "intermediate.pem", "-CAkey", "intermediate.key",
        "-CAcreateserial", "-out", "server.pem", "-days", "2",
        "-extfile", "server.ext",
    )
    # Explicit dates work with both LibreSSL and OpenSSL (negative -days does not).
    (directory / "index.txt").write_text("", encoding="ascii")
    (directory / "serial.txt").write_text("1000\n", encoding="ascii")
    (directory / "ca.cnf").write_text(
        "[ca]\ndefault_ca=offline_ca\n[offline_ca]\n"
        "database=index.txt\nserial=serial.txt\nnew_certs_dir=.\n"
        "certificate=intermediate.pem\nprivate_key=intermediate.key\n"
        "default_md=sha256\npolicy=required_cn\nx509_extensions=server\n"
        "[required_cn]\ncommonName=supplied\n[server]\n"
        + (directory / "server.ext").read_text(encoding="ascii"),
        encoding="ascii",
    )
    _openssl(
        directory, "ca", "-config", "ca.cnf", "-batch", "-notext",
        "-in", "server.csr", "-out", "expired.pem",
        "-startdate", "20200101000000Z", "-enddate", "20210101000000Z",
    )
    return directory


def _context(monkeypatch: pytest.MonkeyPatch, directory: Path, root: str = "root") -> ssl.SSLContext:
    monkeypatch.setattr(tls.certifi, "where", lambda: str(directory / f"{root}.pem"))
    monkeypatch.setattr(tls, "_SUPPLEMENTAL_CHAIN", directory / "intermediate.pem")
    return tls.create_verified_context()


def _handshake(context: ssl.SSLContext, directory: Path, *, hostname: str = "sei.example.test", certificate: str = "server") -> dict:
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # Intentionally send only the leaf, matching the incomplete public chain.
    server_context.load_cert_chain(directory / f"{certificate}.pem", directory / "server.key")
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    complete = [False, False]
    for _ in range(100):
        for index, connection in enumerate((client, server)):
            if not complete[index]:
                try:
                    connection.do_handshake()
                    complete[index] = True
                except ssl.SSLWantReadError:
                    pass
        server_in.write(client_out.read())
        client_in.write(server_out.read())
        if all(complete):
            return client.getpeercert()
    raise AssertionError("Offline TLS handshake did not complete")


def test_completes_chain_to_existing_root(monkeypatch: pytest.MonkeyPatch, certificates: Path) -> None:
    context = _context(monkeypatch, certificates)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert not context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert ("DNS", "sei.example.test") in _handshake(context, certificates)["subjectAltName"]


def test_intermediate_does_not_become_trust_anchor(monkeypatch: pytest.MonkeyPatch, certificates: Path) -> None:
    context = _context(monkeypatch, certificates, "unrelated")
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(context, certificates)


def test_rejects_wrong_hostname(monkeypatch: pytest.MonkeyPatch, certificates: Path) -> None:
    with pytest.raises(ssl.SSLCertVerificationError) as error:
        _handshake(_context(monkeypatch, certificates), certificates, hostname="other.example.test")
    assert error.value.verify_code == 62


def test_rejects_expired_leaf(monkeypatch: pytest.MonkeyPatch, certificates: Path) -> None:
    with pytest.raises(ssl.SSLCertVerificationError) as error:
        _handshake(_context(monkeypatch, certificates), certificates, certificate="expired")
    assert error.value.verify_code == 10


def test_http_factory_has_no_network_or_login(monkeypatch: pytest.MonkeyPatch, certificates: Path) -> None:
    context = _context(monkeypatch, certificates)
    seen: dict = {}
    monkeypatch.setattr(auth, "create_verified_context", lambda: context)
    monkeypatch.setattr(auth.httpx, "Client", lambda **kwargs: seen.update(kwargs) or seen)
    assert auth.create_http_client()["verify"] is context
    assert seen["follow_redirects"] is False
    assert seen["timeout"] == 60
    assert seen["headers"]["User-Agent"] == auth.UA


def test_supplemental_bundle_contains_no_self_signed_root() -> None:
    pem = tls._SUPPLEMENTAL_CHAIN.read_text(encoding="ascii")
    certificates = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
    assert len(certificates) == 3
    for certificate in certificates:
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-subject", "-issuer"],
            input=certificate, capture_output=True, text=True, check=True,
        )
        subject, issuer = result.stdout.strip().splitlines()
        assert subject.split("=", 1)[1] != issuer.split("=", 1)[1]
