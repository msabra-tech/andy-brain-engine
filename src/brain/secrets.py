"""Machine-local secret storage for connector credentials.

Secrets never enter the vault, generated Markdown, or repository configuration.
On Windows, credentials are protected with DPAPI for the currently signed-in user.
Non-Windows development can use a process environment variable, but cannot persist a
credential through this module.
"""
from __future__ import annotations

import ctypes
import os
import re
import tempfile
from ctypes import wintypes
from pathlib import Path

from .config import Config


SECRET_DIRECTORY = Path("data/state/secrets")


class SecretStoreError(RuntimeError):
    """Raised when a connector credential cannot be safely accessed."""


def _normalized_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    if not normalized:
        raise ValueError("secret name must include letters or numbers")
    return normalized


def environment_variable(name: str) -> str:
    return f"ANDY_BRAIN_{_normalized_name(name)}"


def _secret_path(config: Config, name: str) -> Path:
    return config.engine / SECRET_DIRECTORY / f"{_normalized_name(name).lower()}.dpapi"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt_protect(payload: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("persistent connector credentials are supported only on Windows")
    if not payload:
        raise ValueError("secret value cannot be empty")
    source = (ctypes.c_byte * len(payload)).from_buffer_copy(payload)
    input_blob = _DataBlob(len(payload), source)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(ctypes.byref(input_blob), "Andy Brain connector secret", None, None, None, 0x1, ctypes.byref(output_blob)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def _crypt_unprotect(payload: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("persistent connector credentials are supported only on Windows")
    if not payload:
        raise SecretStoreError("connector credential file is empty")
    source = (ctypes.c_byte * len(payload)).from_buffer_copy(payload)
    input_blob = _DataBlob(len(payload), source)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(ctypes.byref(input_blob), None, None, None, None, 0x1, ctypes.byref(output_blob)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def store_secret(config: Config, name: str, value: str) -> Path:
    """Persist a secret for the current Windows account using DPAPI."""
    encrypted = _crypt_protect(value.encode("utf-8"))
    path = _secret_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(encrypted)
        temporary_path = Path(handle.name)
    os.replace(temporary_path, path)
    return path


def get_secret(config: Config, name: str) -> str:
    """Get a secret without ever returning it in status output or a vault artifact."""
    supplied = os.environ.get(environment_variable(name))
    if supplied:
        return supplied
    path = _secret_path(config, name)
    if path.exists():
        return _crypt_unprotect(path.read_bytes()).decode("utf-8")
    raise SecretStoreError(
        f"{name} is not configured. Set {environment_variable(name)} for this process or configure it on Andy's Windows machine."
    )


def secret_configured(config: Config, name: str) -> bool:
    return bool(os.environ.get(environment_variable(name))) or _secret_path(config, name).exists()
