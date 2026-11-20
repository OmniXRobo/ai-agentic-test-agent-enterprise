"""23.13.0: HTTPS with a friendly name (for example https://skyguard-qa/), fully offline.

The application makes its own small certificate authority ("SkyGuard QA Local CA") and a server certificate for the
friendly name, this PC's name, its office-network addresses, localhost and 127.0.0.1. Nothing is downloaded and no
outside service is used. Windows (and so Microsoft Edge) trusts the certificate once the local CA is added to the
"Trusted Root Certification Authorities" store, which ENABLE_HTTPS.cmd does on this PC and TRUST_SKYGUARD_QA.cmd on a
colleague's PC (together with the hosts-file line that maps the friendly name to the server).

The certificates are made with the Python standard library only (RSA-2048, SHA-256, PKCS#1 v1.5 signatures, the
same algorithms Windows' own New-SelfSignedCertificate uses by default), so the offline package needs no new wheel.
The private keys stay in data\\tls and never leave this PC; only the CA certificate (public) is shared.

The plain http://127.0.0.1:<port>/ address keeps working on this PC: the launcher, the browser extension and the
programs on this PC use it.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HOSTNAME_RE = re.compile(r"^(?=.{1,63}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")
DEFAULT_HOSTNAME = "skyguard-qa"
DEFAULT_PORT = 443
CA_NAME = "SkyGuard QA Local CA"
CA_FILE = "SkyGuard_QA_Local_CA.crt"
CERT_FILE = "server.crt"
KEY_FILE = "server.key"
CA_KEY_FILE = "ca.key"
SERVER_DAYS = 825
CA_DAYS = 3650


# ---- settings -------------------------------------------------------------------------------------------------------


def config(settings_data: dict[str, Any]) -> dict[str, Any]:
    https = ((settings_data.get("server") or {}).get("https") or {})
    port = int(https.get("port") or DEFAULT_PORT)
    return {"enabled": bool(https.get("enabled")), "hostname": str(https.get("hostname") or DEFAULT_HOSTNAME),
            "port": port}


def public_url(settings_data: dict[str, Any]) -> str | None:
    c = config(settings_data)
    if not c["enabled"]:
        return None
    return f"https://{c['hostname']}{'' if c['port'] == 443 else ':' + str(c['port'])}/"


def tls_dir(data_root: Path) -> Path:
    return Path(data_root) / "tls"


def files(data_root: Path) -> dict[str, Path]:
    d = tls_dir(data_root)
    return {"ca": d / CA_FILE, "cert": d / CERT_FILE, "key": d / KEY_FILE, "ca_key": d / CA_KEY_FILE}


def ready(data_root: Path) -> bool:
    f = files(data_root)
    return f["cert"].is_file() and f["key"].is_file() and f["ca"].is_file()


def check_hostname(name: str) -> str:
    name = (name or "").strip().lower().rstrip(".")
    if not HOSTNAME_RE.match(name) or name.replace(".", "").isdigit():
        raise ValueError("The name may use letters, digits and dashes (for example skyguard-qa or qa.skyguard.local).")
    return name


# ---- RSA (standard library) -------------------------------------------------------------------------------------------

_SMALL_PRIMES = [p for p in range(3, 2000) if all(p % q for q in range(2, int(p ** 0.5) + 1))]


def _probably_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = 2 + secrets.randbelow(n - 3)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, e: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (3 << (bits - 2)) | 1  # top two bits set: n has the full length
        if candidate % e != 1 and _probably_prime(candidate):
            return candidate


def rsa_key(bits: int = 2048) -> dict[str, int]:
    e = 65537
    while True:
        p, q = _prime(bits // 2, e), _prime(bits // 2, e)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        lam = (p - 1) * (q - 1)
        d = pow(e, -1, lam)
        return {"n": n, "e": e, "d": d, "p": p, "q": q, "dp": d % (p - 1), "dq": d % (q - 1), "qi": pow(q, -1, p)}


def _sign(key: dict[str, int], data: bytes) -> bytes:
    """RSASSA-PKCS1-v1_5 with SHA-256."""
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(data).digest()
    k = (key["n"].bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    m = int.from_bytes(em, "big")
    # CRT, then a check against the public key (a fault must never produce a signature that leaks the key)
    s1, s2 = pow(m, key["dp"], key["p"]), pow(m, key["dq"], key["q"])
    s = s2 + key["q"] * ((key["qi"] * (s1 - s2)) % key["p"])
    if pow(s, key["e"], key["n"]) != m:
        raise RuntimeError("RSA signature self-check failed")
    return s.to_bytes(k, "big")


# ---- DER --------------------------------------------------------------------------------------------------------------


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _len(len(value)) + value


def _int(v: int) -> bytes:
    b = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big")  # a leading zero keeps it positive
    return _tlv(0x02, b)


def _seq(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _set(*items: bytes) -> bytes:
    return _tlv(0x31, b"".join(items))


def _oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    out = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.insert(0, 0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(chunk)
    return _tlv(0x06, out)


def _bits(value: bytes, unused: int = 0) -> bytes:
    return _tlv(0x03, bytes([unused]) + value)


def _octets(value: bytes) -> bytes:
    return _tlv(0x04, value)


def _utf8(text: str) -> bytes:
    return _tlv(0x0C, text.encode("utf-8"))


def _time(t: datetime) -> bytes:
    if t.year < 2050:
        return _tlv(0x17, t.strftime("%y%m%d%H%M%SZ").encode())
    return _tlv(0x18, t.strftime("%Y%m%d%H%M%SZ").encode())


def _explicit(n: int, value: bytes) -> bytes:
    return _tlv(0xA0 + n, value)


SHA256_RSA = _seq(_oid("1.2.840.113549.1.1.11"), b"\x05\x00")


def _name(common: str) -> bytes:
    return _seq(_set(_seq(_oid("2.5.4.10"), _utf8("SkyGuard QA"))), _set(_seq(_oid("2.5.4.3"), _utf8(common))))


def _public_key_info(key: dict[str, int]) -> bytes:
    rsa_pub = _seq(_int(key["n"]), _int(key["e"]))
    return _seq(_seq(_oid("1.2.840.113549.1.1.1"), b"\x05\x00"), _bits(rsa_pub))


def _key_id(key: dict[str, int]) -> bytes:
    return hashlib.sha1(_seq(_int(key["n"]), _int(key["e"]))).digest()  # noqa: S324 - RFC 5280 key identifier


def _ext(oid: str, value: bytes, critical: bool = False) -> bytes:
    return _seq(_oid(oid), *([b"\x01\x01\xff"] if critical else []), _octets(value))


def _certificate(subject: str, subject_key: dict[str, int], issuer: str, issuer_key: dict[str, int], days: int,
                 extensions: list[bytes]) -> bytes:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    serial = secrets.randbits(127) | (1 << 126)
    tbs = _seq(_explicit(0, _int(2)), _int(serial), SHA256_RSA, _name(issuer),
               _seq(_time(now - timedelta(days=1)), _time(now + timedelta(days=days))), _name(subject),
               _public_key_info(subject_key), _explicit(3, _seq(*extensions)))
    return _seq(tbs, SHA256_RSA, _bits(_sign(issuer_key, tbs)))


def _private_key_der(key: dict[str, int]) -> bytes:
    return _seq(_int(0), *(_int(key[k]) for k in ("n", "e", "d", "p", "q", "dp", "dq", "qi")))


def _pem(label: str, der: bytes) -> str:
    b64 = base64.b64encode(der).decode()
    return f"-----BEGIN {label}-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) + \
        f"\n-----END {label}-----\n"


def _pem_key(text: str) -> dict[str, int]:
    body = "".join(line for line in text.splitlines() if line and not line.startswith("-----"))
    der = base64.b64decode(body)
    values: list[int] = []
    i = 2 + (der[1] & 0x7F if der[1] & 0x80 else 0)  # skip the SEQUENCE header
    while i < len(der):
        assert der[i] == 0x02
        n = der[i + 1]
        i += 2
        if n & 0x80:
            count = n & 0x7F
            n = int.from_bytes(der[i:i + count], "big")
            i += count
        values.append(int.from_bytes(der[i:i + n], "big"))
        i += n
    names = ("version", "n", "e", "d", "p", "q", "dp", "dq", "qi")
    return dict(zip(names, values))


# ---- the certificates -------------------------------------------------------------------------------------------------


def names_for(hostname: str) -> tuple[list[str], list[str]]:
    """DNS names and IP addresses the server certificate covers."""
    dns = [hostname, "localhost"]
    pc = socket.gethostname().strip().lower()
    if pc and HOSTNAME_RE.match(pc):
        dns.append(pc)
    ips = ["127.0.0.1"]
    try:
        from .api.auth import addresses
        ips += addresses()[1]
    except Exception:
        pass
    return list(dict.fromkeys(dns)), list(dict.fromkeys(ips))


def _san(dns: list[str], ips: list[str]) -> bytes:
    entries = [_tlv(0x82, d.encode("ascii")) for d in dns]
    for ip in ips:
        try:
            entries.append(_tlv(0x87, ipaddress.ip_address(ip).packed))
        except ValueError:
            continue
    return _seq(*entries)


def _write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="ascii")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def make_certificates(data_root: Path, hostname: str, *, bits: int = 2048) -> dict[str, Any]:
    """Create (or renew) the server certificate; the local CA is made once and kept, so colleagues trust it once."""
    hostname = check_hostname(hostname)
    f = files(data_root)
    f["ca"].parent.mkdir(parents=True, exist_ok=True)
    if f["ca"].is_file() and f["ca_key"].is_file():
        ca_key = _pem_key(f["ca_key"].read_text(encoding="ascii"))
        made_ca = False
    else:
        ca_key = rsa_key(bits)
        ca_ext = [_ext("2.5.29.19", _seq(b"\x01\x01\xff", _int(0)), critical=True),
                  _ext("2.5.29.15", _bits(b"\x06", 1), critical=True),
                  _ext("2.5.29.14", _octets(_key_id(ca_key)))]
        ca_der = _certificate(CA_NAME, ca_key, CA_NAME, ca_key, CA_DAYS, ca_ext)
        _write_private(f["ca_key"], _pem("RSA PRIVATE KEY", _private_key_der(ca_key)))
        f["ca"].write_text(_pem("CERTIFICATE", ca_der), encoding="ascii")
        made_ca = True
    server_key = rsa_key(bits)
    dns, ips = names_for(hostname)
    ext = [_ext("2.5.29.19", _seq(), critical=True),
           _ext("2.5.29.15", _bits(b"\xa0", 5), critical=True),
           _ext("2.5.29.37", _seq(_oid("1.3.6.1.5.5.7.3.1"))),
           _ext("2.5.29.17", _san(dns, ips)),
           _ext("2.5.29.14", _octets(_key_id(server_key))),
           _ext("2.5.29.35", _seq(_tlv(0x80, _key_id(ca_key))))]
    der = _certificate(hostname, server_key, CA_NAME, ca_key, SERVER_DAYS, ext)
    _write_private(f["key"], _pem("RSA PRIVATE KEY", _private_key_der(server_key)))
    f["cert"].write_text(_pem("CERTIFICATE", der) + f["ca"].read_text(encoding="ascii"), encoding="ascii")
    return {"hostname": hostname, "dns": dns, "ips": ips, "new_ca": made_ca, "ca_file": str(f["ca"]),
            "valid_days": SERVER_DAYS}


def ca_fingerprint(data_root: Path) -> str | None:
    f = files(data_root)["ca"]
    if not f.is_file():
        return None
    body = "".join(line for line in f.read_text(encoding="ascii").splitlines() if line and not line.startswith("-----"))
    return hashlib.sha256(base64.b64decode(body)).hexdigest().upper()


def status(data_root: Path, settings_data: dict[str, Any]) -> dict[str, Any]:
    c = config(settings_data)
    return {**c, "ready": ready(data_root), "url": public_url(settings_data), "ca_sha256": ca_fingerprint(data_root),
            "ca_file": str(files(data_root)["ca"])}


__all__ = ["config", "public_url", "make_certificates", "status", "ready", "files", "check_hostname"]
