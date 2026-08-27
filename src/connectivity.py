"""Ping-based dual-stack preference test."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from policy import Preference

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
_PING_WAIT_MS = 2000
_PING_TIMEOUT_S = 5
VERIFY_TIMEOUT_S = 15

# Clash / Surge Fake-IP (IETF benchmarking 198.18/15) and common IPv6 placeholders.
_FAKE_IP_NETS = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("fdfe:dcba:9876::/48"),
    ipaddress.ip_network("2001:db8::/32"),
)

_NO_HOST = re.compile(
    r"could not find host|找不到主机|未知主机|Name or service not known",
    re.IGNORECASE,
)

_PING_FAIL = re.compile(
    r"Destination host unreachable|无法访问目标主机|"
    r"General failure|一般性故障|一般故障|"
    r"transmit failed|传输失败|"
    r"100%\s*(?:loss|丢失)|"
    r"Request timed out|请求超时",
    re.IGNORECASE,
)

_PING_OK = re.compile(
    r"TTL=\d|(?:Reply from|来自 ).+(?:time=\d|时间\s*=\s*\d)|"
    r"Reply from.+\btime=",
    re.IGNORECASE,
)

# TCPTest dual-stack probe: ping vv.tcptest.cn
PROBE_TCPTEST = "vv.tcptest.cn"

# Domestic first, then international fallback.
DEFAULT_TEST_HOSTS: tuple[str, ...] = (
    PROBE_TCPTEST,
    "v4v6.ipgg.cn",
    "www.baidu.com",
    "www.qq.com",
    "dns.google",
    "one.one.one.one",
    "ietf.org",
    "www.cloudflare.com",
)

AUTO_LABEL = "自动（国内优先）"
CUSTOM_LABEL = "自定义…"

# Dropdown options: (display_label, host_or_None_for_auto_or_custom)
PROBE_CHOICES: tuple[tuple[str, Optional[str]], ...] = (
    (AUTO_LABEL, None),
    (f"{PROBE_TCPTEST}（国内）", PROBE_TCPTEST),
    ("v4v6.ipgg.cn（国内）", "v4v6.ipgg.cn"),
    ("www.baidu.com（国内）", "www.baidu.com"),
    ("www.qq.com（国内）", "www.qq.com"),
    ("dns.google（国外）", "dns.google"),
    ("one.one.one.one（国外）", "one.one.one.one"),
    ("ietf.org（国外）", "ietf.org"),
    ("www.cloudflare.com（国外）", "www.cloudflare.com"),
    (CUSTOM_LABEL, ""),
)

_IP_IN_BRACKETS = re.compile(r"\[([0-9a-fA-F:.]+)\]")


class Family(Enum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"


@dataclass(frozen=True)
class ConnectTestResult:
    ok: bool
    host: str
    family: Optional[Family]
    peer_ip: str
    ipv4_ip: str
    ipv6_ip: str
    message: str
    preference: Preference
    matches_policy: Optional[bool] = None
    hint: str = ""
    kind: str = ""
    title: str = ""
    warn: bool = False

    @property
    def preference_label(self) -> str:
        if self.preference is Preference.IPV4:
            return "IPv4"
        if self.preference is Preference.IPV6:
            return "IPv6"
        return "—"


def _family_of(ip: str) -> Family:
    return Family.IPV6 if ipaddress.ip_address(ip).version == 6 else Family.IPV4


def _is_fake_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _FAKE_IP_NETS)


def _hint_fake_ip(ip: str) -> str:
    return f"解析到 Fake-IP（{ip}）。多半是 Clash / Surge 劫持了 DNS，请关闭系统代理或设直连。"


def _hint_no_host() -> str:
    return "域名解析失败。请检查网络和 DNS。"


def make_timeout_result(host: str = "") -> ConnectTestResult:
    return ConnectTestResult(
        ok=False,
        host=host,
        family=None,
        peer_ip="",
        ipv4_ip="",
        ipv6_ip="",
        message="验证超时",
        preference=Preference.UNKNOWN,
        hint=f"超过 {VERIFY_TIMEOUT_S} 秒未完成，请检查网络后重试。",
        kind="timeout",
        title="验证超时",
    )


def _hint_no_ipv4() -> str:
    return "IPv4 不通。可能是站点没有 IPv4，或本机 / 代理拦截了 IPv4。"


def _hint_no_ipv6() -> str:
    return "IPv6 不通。可能是本机关闭了 IPv6、运营商未分配，或代理拦截。"


def _hint_no_reply() -> str:
    return "已解析到地址，但 Ping 无响应。可能是防火墙或代理拦截了 ICMP。"


def _hint_mismatch(expected: Preference, actual: Preference) -> str:
    want = "IPv4" if expected is Preference.IPV4 else "IPv6"
    got = "IPv4" if actual is Preference.IPV4 else "IPv6"
    return f"策略是 {want} 优先，但实际走了 {got}。可能是代理改写了 DNS。"


def _summarize(
    expected: Optional[Preference],
    v4_ok: bool,
    v6_ok: bool,
    actual: Preference,
) -> tuple[str, str, Optional[bool], str, bool]:
    """Return title, kind, matches_policy, hint, warn."""
    if v4_ok and v6_ok:
        if expected is Preference.IPV4:
            if actual is Preference.IPV4:
                return "IPv4 优先  ·  双栈均通", "both_ok", True, "", False
            return (
                "IPv4 优先，但实际走 IPv6",
                "mismatch",
                False,
                _hint_mismatch(expected, actual),
                True,
            )
        if expected is Preference.IPV6:
            if actual is Preference.IPV6:
                return "IPv6 优先  ·  双栈均通", "both_ok", True, "", False
            return (
                "IPv6 优先，但实际走 IPv4",
                "mismatch",
                False,
                _hint_mismatch(expected, actual),
                True,
            )
        got = "IPv4" if actual is Preference.IPV4 else "IPv6"
        return f"双栈均通  ·  实际 {got}", "both_ok", None, "", False

    if v4_ok and not v6_ok:
        hint = _hint_no_ipv6()
        if expected is Preference.IPV6:
            return "IPv6 优先，但 IPv6 不通", "v6_down", False, hint, True
        if expected is Preference.IPV4:
            return "IPv4 优先，但 IPv6 不通", "v6_down", True, hint, True
        return "仅 IPv4 通", "v6_down", None, hint, True

    if v6_ok and not v4_ok:
        hint = _hint_no_ipv4()
        if expected is Preference.IPV4:
            return "IPv4 优先，但 IPv4 不通", "v4_down", False, hint, True
        if expected is Preference.IPV6:
            return "IPv6 优先，但 IPv4 不通", "v4_down", True, hint, True
        return "仅 IPv6 通", "v4_down", None, hint, True

    return "验证失败", "unreachable", None, "", False


def decode_console(data: bytes) -> str:
    """Decode ping/netsh output on both GBK and UTF-8 Windows locales."""
    if not data:
        return ""
    scored: list[tuple[int, str]] = []
    for enc in ("utf-8", "gbk"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        score = 0
        for kw in (
            "Pinging",
            "正在 Ping",
            "Reply from",
            "来自",
            "TTL=",
            "找不到主机",
            "could not find",
            "Precedence",
            "Prefix",
            "优先级",
            "前缀",
        ):
            if kw in text:
                score += 3
        score -= text.count("\ufffd") * 8
        scored.append((score, text))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    return data.decode("utf-8", errors="replace")


def _normalize_host(raw: str) -> str:
    host = (raw or "").strip()
    if not host:
        return ""
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE)
    if host.startswith("[") and "]" in host:
        inner = host[1 : host.index("]")]
        try:
            ipaddress.IPv6Address(inner)
            return inner
        except ValueError:
            pass
    host = host.split("/")[0].split("?")[0].split("#")[0].strip()
    if host.count(":") == 1:
        name, port = host.rsplit(":", 1)
        if port.isdigit():
            host = name
    if not host or host.startswith("-") or any(ch.isspace() for ch in host):
        return ""
    return host


def _run_ping(
    host: str,
    *,
    family: Optional[str] = None,
    count: int = 1,
    timeout_s: float = _PING_TIMEOUT_S,
) -> str:
    cmd = ["ping", "-n", str(count), "-w", str(_PING_WAIT_MS)]
    if family == "4":
        cmd.append("-4")
    elif family == "6":
        cmd.append("-6")
    cmd.append(host)
    timeout_s = max(1.0, float(timeout_s))
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            creationflags=_NO_WINDOW,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        proc = getattr(exc, "process", None)
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        return ""
    except OSError:
        return ""
    return decode_console((completed.stdout or b"") + (completed.stderr or b""))


def _extract_resolved_ip(output: str) -> Optional[str]:
    match = _IP_IN_BRACKETS.search(output)
    if not match:
        return None
    ip = match.group(1).strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


def _extract_ping_ip(output: str) -> Optional[str]:
    ip = _extract_resolved_ip(output)
    if not ip:
        return None
    if _PING_FAIL.search(output):
        return None
    if not _PING_OK.search(output):
        return None
    return ip


def test_host_via_ping(
    host: str,
    expected: Optional[Preference] = None,
    deadline: Optional[float] = None,
) -> ConnectTestResult:
    host = _normalize_host(host)
    if not host:
        return ConnectTestResult(
            ok=False,
            host="",
            family=None,
            peer_ip="",
            ipv4_ip="",
            ipv6_ip="",
            message="域名无效",
            preference=Preference.UNKNOWN,
            hint="请输入合法域名或 IP，不要以 - 开头。",
            kind="empty",
            title="验证失败",
        )

    def _slice() -> float:
        if deadline is None:
            return float(_PING_TIMEOUT_S)
        left = deadline - time.monotonic()
        if left <= 0.2:
            return 0.0
        return min(float(_PING_TIMEOUT_S), left)

    def _ping(family: Optional[str] = None) -> str:
        slice_s = _slice()
        if slice_s <= 0:
            return ""
        return _run_ping(host, family=family, timeout_s=slice_s)

    if _slice() <= 0:
        return make_timeout_result(host)

    out4 = _ping("4")
    out6 = _ping("6")
    ip4 = _extract_ping_ip(out4)
    ip6 = _extract_ping_ip(out6)
    if ip4 and _family_of(ip4) is not Family.IPV4:
        ip4 = None
    if ip6 and _family_of(ip6) is not Family.IPV6:
        ip6 = None

    fake = next((ip for ip in (ip4, ip6) if ip and _is_fake_ip(ip)), None)
    if fake:
        return ConnectTestResult(
            ok=False,
            host=host,
            family=None,
            peer_ip=fake,
            ipv4_ip=ip4 or "",
            ipv6_ip=ip6 or "",
            message="Fake-IP 劫持",
            preference=Preference.UNKNOWN,
            hint=_hint_fake_ip(fake),
            kind="fake_ip",
            title="验证失败",
        )

    if not ip4 and not ip6:
        if deadline is not None and time.monotonic() >= deadline:
            return make_timeout_result(host)
        no_host = bool(_NO_HOST.search(out4) or _NO_HOST.search(out6))
        return ConnectTestResult(
            ok=False,
            host=host,
            family=None,
            peer_ip="",
            ipv4_ip="",
            ipv6_ip="",
            message="无法解析" if no_host else "IPv4 / IPv6 均不通",
            preference=Preference.UNKNOWN,
            hint=_hint_no_host() if no_host else "探测站 IPv4 和 IPv6 都无法访问。",
            kind="no_host" if no_host else "unreachable",
            title="验证失败",
        )

    chosen = _extract_ping_ip(_ping(None))
    if not chosen or _is_fake_ip(chosen):
        chosen = ip6 if (expected is Preference.IPV6 and ip6) else (ip4 or ip6)
    if not chosen:
        return ConnectTestResult(
            ok=False,
            host=host,
            family=None,
            peer_ip="",
            ipv4_ip=ip4 or "",
            ipv6_ip=ip6 or "",
            message="未能取得实际返回地址",
            preference=Preference.UNKNOWN,
            hint=_hint_no_reply(),
            kind="no_reply",
            title="验证失败",
        )

    family = _family_of(chosen)
    actual = Preference.IPV4 if family is Family.IPV4 else Preference.IPV6
    title, kind, matches, hint, warn = _summarize(
        expected, bool(ip4), bool(ip6), actual
    )
    return ConnectTestResult(
        ok=True,
        host=host,
        family=family,
        peer_ip=chosen,
        ipv4_ip=ip4 or "",
        ipv6_ip=ip6 or "",
        message=title,
        preference=actual,
        matches_policy=matches,
        hint=hint,
        kind=kind,
        title=title,
        warn=warn,
    )


def hosts_for_probe_choice(
    label: str,
    custom_host: str = "",
) -> tuple[str, ...]:
    """Map dropdown label to host list."""
    if label == CUSTOM_LABEL:
        host = _normalize_host(custom_host)
        return (host,) if host else ()
    for display, host in PROBE_CHOICES:
        if display == label:
            if host is None:
                return DEFAULT_TEST_HOSTS
            if host == "":
                host = _normalize_host(custom_host)
                return (host,) if host else ()
            return (host,)
    return DEFAULT_TEST_HOSTS


def run_connect_test(
    hosts: tuple[str, ...] = DEFAULT_TEST_HOSTS,
    expected: Optional[Preference] = None,
    timeout_s: float = VERIFY_TIMEOUT_S,
) -> ConnectTestResult:
    """Try hosts in order: domestic first, then international."""
    if not hosts:
        return ConnectTestResult(
            ok=False,
            host="",
            family=None,
            peer_ip="",
            ipv4_ip="",
            ipv6_ip="",
            message="请输入自定义探测站域名",
            preference=Preference.UNKNOWN,
            hint="请输入可 Ping 的双栈域名，例如 vv.tcptest.cn。",
            kind="empty",
            title="验证失败",
        )

    deadline = time.monotonic() + max(1.0, float(timeout_s))
    first_fail: Optional[ConnectTestResult] = None
    for host in hosts:
        if time.monotonic() >= deadline:
            return first_fail or make_timeout_result(host)
        try:
            result = test_host_via_ping(host, expected=expected, deadline=deadline)
        except Exception as exc:
            result = ConnectTestResult(
                ok=False,
                host=host,
                family=None,
                peer_ip="",
                ipv4_ip="",
                ipv6_ip="",
                message="探测异常",
                preference=Preference.UNKNOWN,
                hint=f"探测过程出错：{exc}",
                kind="error",
                title="验证失败",
            )
        if result.kind == "timeout":
            return result
        if result.ok:
            return result
        if first_fail is None:
            first_fail = result
    return first_fail or ConnectTestResult(
        ok=False,
        host="",
        family=None,
        peer_ip="",
        ipv4_ip="",
        ipv6_ip="",
        message="探测失败",
        preference=Preference.UNKNOWN,
        hint="所有探测站都无法访问。",
        kind="unreachable",
        title="验证失败",
    )
