"""Антивирусная проверка перед разбором.

ClamdScanner говорит с clamd по unix-сокету (протокол INSTREAM). Если сканер не
настроен или недоступен, результат unavailable, и файл не разбирается, пока
явно не включён демо-режим без проверки.
"""

import socket
import struct
from typing import Literal, Protocol

ScanResult = Literal["clean", "infected", "unavailable"]


class Scanner(Protocol):
    def scan(self, data: bytes) -> ScanResult: ...


class NoScanner:
    def scan(self, data: bytes) -> ScanResult:
        return "unavailable"


class ClamdScanner:
    def __init__(self, socket_path: str, timeout_s: float = 10.0):
        self.socket_path = socket_path
        self.timeout_s = timeout_s

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout_s)
                s.connect(self.socket_path)
                s.sendall(b"zINSTREAM\0")
                for start in range(0, len(data), 64 * 1024):
                    chunk = data[start:start + 64 * 1024]
                    s.sendall(struct.pack(">I", len(chunk)) + chunk)
                s.sendall(struct.pack(">I", 0))
                reply = s.recv(4096).decode("utf-8", "ignore")
        except OSError:
            return "unavailable"
        if reply.rstrip("\0").endswith("OK"):
            return "clean"
        if "FOUND" in reply:
            return "infected"
        return "unavailable"


import os
from pathlib import Path


def scan_command(command: tuple[str, ...], path: Path) -> list[str]:
    if command != ("windows-defender",):
        return [*command, str(path.resolve())]
    if os.name != "nt":
        raise OSError("Microsoft Defender requires Windows")
    platform = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows Defender/Platform"
    candidates = sorted(platform.glob("*/MpCmdRun.exe"), reverse=True)
    candidates.append(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Windows Defender/MpCmdRun.exe")
    executable = next((candidate for candidate in candidates if candidate.is_file()), None)
    if executable is None:
        raise OSError("Microsoft Defender not installed")
    # DisableRemediation also ignores exclusions. Exit 0 then means no threat;
    # exit 2 combines detections and errors and must NEVER release the file.
    return [str(executable), "-Scan", "-ScanType", "3", "-DisableRemediation", "-File", str(path.resolve())]
