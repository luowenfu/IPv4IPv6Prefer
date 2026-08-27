"""IPv6 prefix policy helpers for preferring IPv4 or IPv6."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional


IPV4_MAPPED = "::ffff:0:0/96"
IPV6_GLOBAL = "::/0"

# Default Windows values
DEFAULT_IPV4_MAPPED_PREC = 35
DEFAULT_IPV4_MAPPED_LABEL = 4
IPV4_PREFER_PREC = 46


class Preference(Enum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PolicyEntry:
    precedence: int
    label: int
    prefix: str


@dataclass(frozen=True)
class PreferenceStatus:
    preference: Preference
    ipv4_mapped: Optional[PolicyEntry]
    ipv6_global: Optional[PolicyEntry]
    raw: str


class PolicyError(RuntimeError):
    """Raised when netsh fails or output cannot be parsed."""


def _run_netsh(args: list[str]) -> str:
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        completed = subprocess.run(
            ["netsh", *args],
            capture_output=True,
            creationflags=flags,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise PolicyError("netsh 超时，请稍后重试") from exc
    except OSError as exc:
        raise PolicyError(f"无法运行 netsh: {exc}") from exc

    from connectivity import decode_console

    output = decode_console((completed.stdout or b"") + (completed.stderr or b""))
    if completed.returncode != 0:
        detail = output.strip() or f"exit code {completed.returncode}"
        raise PolicyError(f"netsh 失败: {detail}")
    return output


def parse_prefix_policies(text: str) -> list[PolicyEntry]:
    """Parse `netsh interface ipv6 show prefixpolicies` output."""
    entries: list[PolicyEntry] = []
    # English/Chinese: "     40      1 ::/0"
    pattern = re.compile(
        r"^\s*(\d+)\s+(\d+)\s+([0-9a-fA-F:.]+/\d+)\s*$",
        re.MULTILINE,
    )
    for match in pattern.finditer(text.replace("\r\n", "\n")):
        entries.append(
            PolicyEntry(
                precedence=int(match.group(1)),
                label=int(match.group(2)),
                prefix=match.group(3).lower(),
            )
        )
    return entries


def _find_prefix(entries: list[PolicyEntry], prefix: str) -> Optional[PolicyEntry]:
    target = prefix.lower()
    for entry in entries:
        if entry.prefix == target:
            return entry
    return None


def get_preference() -> PreferenceStatus:
    raw = _run_netsh(["interface", "ipv6", "show", "prefixpolicies"])
    entries = parse_prefix_policies(raw)
    ipv4_mapped = _find_prefix(entries, IPV4_MAPPED)
    ipv6_global = _find_prefix(entries, IPV6_GLOBAL)

    if ipv4_mapped is None or ipv6_global is None:
        preference = Preference.UNKNOWN
    elif ipv4_mapped.precedence > ipv6_global.precedence:
        preference = Preference.IPV4
    else:
        preference = Preference.IPV6

    return PreferenceStatus(
        preference=preference,
        ipv4_mapped=ipv4_mapped,
        ipv6_global=ipv6_global,
        raw=raw,
    )


def set_ipv4_prefer() -> None:
    """Raise IPv4-mapped precedence above default IPv6 global (40)."""
    _run_netsh(
        [
            "interface",
            "ipv6",
            "set",
            "prefixpolicy",
            IPV4_MAPPED,
            str(IPV4_PREFER_PREC),
            str(DEFAULT_IPV4_MAPPED_LABEL),
        ]
    )


def set_ipv6_prefer() -> None:
    """Restore default IPv4-mapped precedence (IPv6 preferred)."""
    _run_netsh(
        [
            "interface",
            "ipv6",
            "set",
            "prefixpolicy",
            IPV4_MAPPED,
            str(DEFAULT_IPV4_MAPPED_PREC),
            str(DEFAULT_IPV4_MAPPED_LABEL),
        ]
    )
