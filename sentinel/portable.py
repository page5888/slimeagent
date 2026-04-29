"""Slime Core portable export / restore (manifesto 第三守則, day-1).

The manifesto commits to "核心格式公開、可移植、可加密匯出". Until
this module shipped, that ✅ in the manifesto status table was
technically dishonest: the JSON files happened to live in
~/.hermes/ but no end-user UI existed to bundle, encrypt, or
restore them. This module pays that debt.

Why this matters before any cloud / sync work:
  • If we ship hosting BEFORE the user can verify "my Slime is
    portable without you", we become Replika. The whole 第三守則
    only holds if a user can survive our company dying.
  • The format is documented in docs/SLIME_CORE_FORMAT.md to byte
    precision so any third party (foundation, the user themselves
    in Rust / JS) can write a reader without reading our code.

Format (v1, see SPEC for byte-exact details):
    [0..6)   magic  = b"SLIME\\x00"
    [6]      format_version uint8 = 1
    [7]      reserved uint8 = 0
    [8..10)  header_len uint16 BE
    [10..)   header_json (UTF-8, length = header_len)
    [next..) ciphertext (AES-256-GCM; tag appended; key from scrypt)

Threat model: the file is intended to be safe to upload to opaque
cloud storage or share over insecure channels. The passphrase
never leaves the user's machine; nobody else (including this
project's authors) can decrypt without it. Tradeoff: forgotten
passphrase = unrecoverable data, same as any sane E2EE scheme.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.portable")

# These are NOT secret — they're the file format. Changing them
# breaks backward compatibility forever, so don't.
MAGIC = b"SLIME\x00"           # 6 bytes
FORMAT_VERSION = 1             # uint8
PREAMBLE_LEN = 8               # magic(6) + version(1) + reserved(1)

# scrypt cost parameters. n=2^15 was the OWASP-recommended default
# at the time of writing; ~80MB peak RAM, ~150ms on a modern CPU.
# Strong enough that brute-forcing a 12-char-ish passphrase is
# expensive without making honest restore feel slow.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32              # AES-256
SCRYPT_MAXMEM = 256 * 1024 * 1024  # generous, keeps n=2^16 future-room

HERMES_DIR = Path.home() / ".hermes"


class PortableError(Exception):
    """Surface to the user. Carries a friendly message; original
    exception (if any) is chained for the audit log."""


@dataclass
class ExportResult:
    path: Path
    bytes_written: int
    files_included: int


@dataclass
class ImportResult:
    path: Path
    bytes_read: int
    files_extracted: int
    header: dict
    backup_dir: Optional[Path]


# ─── Crypto helpers ──────────────────────────────────────────────


def _derive_key(passphrase: str, salt: bytes,
                n: int = SCRYPT_N, r: int = SCRYPT_R,
                p: int = SCRYPT_P, dklen: int = SCRYPT_DKLEN) -> bytes:
    """scrypt-derived AES key. Stdlib hashlib.scrypt; no third-party
    KDF in the path so a future Python without `cryptography` could
    still verify export integrity if needed."""
    if not passphrase:
        raise PortableError("加密通行碼不能空白")
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=n, r=r, p=p,
        dklen=dklen,
        maxmem=SCRYPT_MAXMEM,
    )


def _aesgcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).encrypt(nonce, plaintext, associated_data=None)


def _aesgcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
    except InvalidTag as e:
        # Either wrong passphrase or corrupted file. Both feel the same
        # to AES-GCM by design (no oracle for distinguishing).
        raise PortableError("解密失敗：通行碼錯誤或檔案損壞") from e


# ─── Zip plaintext bundling ──────────────────────────────────────


def _bundle_hermes_dir(src: Path) -> tuple[bytes, int]:
    """Walk src and produce an in-memory zip. Returns (bytes, count)."""
    if not src.exists() or not src.is_dir():
        raise PortableError(f"找不到 ~/.hermes 目錄：{src}")

    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(src.rglob("*")):
            if path.is_dir():
                continue
            try:
                arcname = path.relative_to(src).as_posix()
                z.write(path, arcname)
                count += 1
            except OSError as e:
                # Skip transient unreadable files (lock contention,
                # AV scan races) instead of failing the whole export.
                log.warning(f"export skipped {path}: {e}")
    return buf.getvalue(), count


def _extract_zip_over(plaintext_zip: bytes, dst: Path) -> int:
    """Extract zip bytes into dst. Returns file count. Caller is
    responsible for backing up dst first if it cares about
    overwriting."""
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(io.BytesIO(plaintext_zip), "r") as z:
        for member in z.namelist():
            # zipfile.extract resolves '..' safely in modern Python,
            # but we still validate to fail loud on hostile archives.
            target = dst / member
            try:
                target.resolve().relative_to(dst.resolve())
            except ValueError as e:
                raise PortableError(
                    f"備份檔包含可疑路徑（{member}），停止還原"
                ) from e
            z.extract(member, dst)
            count += 1
    return count


# ─── Public API ──────────────────────────────────────────────────


def export_to(passphrase: str, dst_path: Path,
              src_dir: Path = HERMES_DIR) -> ExportResult:
    """Encrypt-bundle src_dir to dst_path. Atomic write via temp file
    so a crashed export doesn't leave a half-written .slime in the
    way of next time."""
    plaintext, file_count = _bundle_hermes_dir(src_dir)

    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)
    ciphertext = _aesgcm_encrypt(key, nonce, plaintext)

    header = {
        "format_version": FORMAT_VERSION,
        "kdf": "scrypt",
        "kdf_params": {
            "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
            "dklen": SCRYPT_DKLEN,
        },
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        # informational, NOT used in decryption — useful for the
        # "what is this file" pre-decrypt UI in future readers.
        "created_at": int(time.time()),
        "creator": "sentinel",
    }
    header_bytes = json.dumps(
        header, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    if len(header_bytes) > 0xFFFF:
        raise PortableError("header 太大（內部錯誤）")

    out = bytearray()
    out += MAGIC
    out.append(FORMAT_VERSION)
    out.append(0)  # reserved
    out += len(header_bytes).to_bytes(2, "big")
    out += header_bytes
    out += ciphertext

    dst_path = Path(dst_path)
    tmp = dst_path.with_suffix(dst_path.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(bytes(out))
    os.replace(tmp, dst_path)

    return ExportResult(
        path=dst_path,
        bytes_written=len(out),
        files_included=file_count,
    )


def _parse_envelope(data: bytes) -> tuple[dict, bytes]:
    """Verify magic + version, return (header_dict, ciphertext)."""
    if len(data) < PREAMBLE_LEN + 2:
        raise PortableError("檔案過短，不是有效的 .slime")
    if data[:6] != MAGIC:
        raise PortableError("magic bytes 不符，不是 .slime 格式")
    version = data[6]
    if version != FORMAT_VERSION:
        raise PortableError(
            f"格式版本不支援：檔案 v{version}，本程式 v{FORMAT_VERSION}"
        )
    header_len = int.from_bytes(data[PREAMBLE_LEN:PREAMBLE_LEN + 2], "big")
    header_start = PREAMBLE_LEN + 2
    header_end = header_start + header_len
    if header_end > len(data):
        raise PortableError("header 長度超過檔案大小，檔案損壞")
    try:
        header = json.loads(data[header_start:header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PortableError(f"header 解析失敗：{e}") from e
    return header, bytes(data[header_end:])


def import_from(passphrase: str, src_path: Path,
                dst_dir: Path = HERMES_DIR,
                backup: bool = True) -> ImportResult:
    """Decrypt + extract src_path into dst_dir. If backup=True (the
    default and what the GUI uses), the existing dst_dir is moved
    aside to ~/.hermes.bak.<ts>/ before extraction so the user can
    revert by hand if the restored data turns out to be wrong."""
    src_path = Path(src_path)
    data = src_path.read_bytes()
    header, ciphertext = _parse_envelope(data)

    # Decryption param resolution. Allow the file to dictate what
    # KDF cost to use so a future v1 reader written for tighter
    # parameters can still consume looser old files.
    kdf = header.get("kdf", "")
    if kdf != "scrypt":
        raise PortableError(f"不支援的 KDF：{kdf!r}")
    params = header.get("kdf_params") or {}
    try:
        salt = base64.b64decode(header["salt"])
        nonce = base64.b64decode(header["nonce"])
    except (KeyError, ValueError) as e:
        raise PortableError(f"header 缺欄位或 base64 損壞：{e}") from e
    key = _derive_key(
        passphrase, salt,
        n=int(params.get("n", SCRYPT_N)),
        r=int(params.get("r", SCRYPT_R)),
        p=int(params.get("p", SCRYPT_P)),
        dklen=int(params.get("dklen", SCRYPT_DKLEN)),
    )
    plaintext = _aesgcm_decrypt(key, nonce, ciphertext)

    backup_dir: Optional[Path] = None
    if backup and dst_dir.exists():
        backup_dir = dst_dir.with_name(dst_dir.name + ".bak."
                                       + str(int(time.time())))
        try:
            os.rename(dst_dir, backup_dir)
        except OSError as e:
            raise PortableError(
                f"備份目前的 ~/.hermes 失敗，停止還原以免覆蓋：{e}"
            ) from e

    try:
        file_count = _extract_zip_over(plaintext, dst_dir)
    except Exception:
        # Roll back: restore the previous state.
        if backup_dir is not None and backup_dir.exists():
            try:
                if dst_dir.exists():
                    import shutil
                    shutil.rmtree(dst_dir)
                os.rename(backup_dir, dst_dir)
            except OSError as rollback_err:
                log.error(f"restore rollback failed: {rollback_err}")
        raise

    return ImportResult(
        path=src_path,
        bytes_read=len(data),
        files_extracted=file_count,
        header=header,
        backup_dir=backup_dir,
    )
