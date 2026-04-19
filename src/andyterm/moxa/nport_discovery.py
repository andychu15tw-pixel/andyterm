"""moxa/nport_discovery.py — Moxa NPort 裝置掃描 (保守版)。

結論先寫:
    - 採取保守策略:主要走 RFC2217,不自幹 NPort 私有 search protocol。
    - scan_nport_by_network(subnet) 用 socket port 4001 probe 找可疑 NPort。
    - 使用者手動輸入 NPort IP 仍是最可靠的方式。
    - 無任何 Qt 依賴,亦不 import core/。

分層原則:本模組位於 moxa/,不得 import Qt 模組或 core/。
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

__all__ = ["NPortInfo", "scan_nport_by_network"]

# Moxa NPort 預設 TCP port
_NPORT_PROBE_PORT = 4001
_PROBE_TIMEOUT = 0.5   # 每台 host 的 timeout (秒)
_MAX_WORKERS = 64       # 並行掃描執行緒數


@dataclass
class NPortInfo:
    """Moxa NPort 裝置資訊 (TCP Server mode)。

    欄位:
        ip: 裝置 IP 位址。
        port: TCP port (預設 4001)。
        model: 推測的型號 (目前固定為 "Moxa NPort" — 未做進一步識別)。
        rfc2217_url: 可直接傳給 pyserial 的 rfc2217:// URL。
    """

    ip: str
    port: int
    model: str
    rfc2217_url: str

    def __repr__(self) -> str:
        return f"NPortInfo(ip={self.ip!r}, port={self.port}, model={self.model!r})"


def _probe_host(ip: str, port: int, timeout: float) -> NPortInfo | None:
    """嘗試 TCP 連線 ip:port,成功則回傳 NPortInfo,否則回 None。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return NPortInfo(
                ip=ip,
                port=port,
                model="Moxa NPort",
                rfc2217_url=f"rfc2217://{ip}:{port}",
            )
    except OSError:
        return None


def scan_nport_by_network(
    subnet: str,
    probe_port: int = _NPORT_PROBE_PORT,
    timeout: float = _PROBE_TIMEOUT,
    max_workers: int = _MAX_WORKERS,
) -> list[NPortInfo]:
    """掃描子網路,回傳可能的 Moxa NPort 裝置列表。

    結論:以 TCP connect 方式探測 ip:probe_port,有回應即列為疑似 NPort。
    不保證 100% 準確 — 其他服務也可能在 4001 port 監聽。

    參數:
        subnet: CIDR 表示法,例如 "192.168.127.0/24"。
        probe_port: 探測用的 TCP port (預設 4001)。
        timeout: 每台 host 的連線逾時 (秒,預設 0.5)。
        max_workers: 並行執行緒上限 (預設 64)。

    回傳:
        NPortInfo 物件列表 (可能為空)。
    """
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return []

    hosts = list(network.hosts())
    results: list[NPortInfo] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_probe_host, str(h), probe_port, timeout): str(h)
            for h in hosts
        }
        for future in as_completed(futures):
            info = future.result()
            if info is not None:
                results.append(info)

    results.sort(key=lambda x: ipaddress.ip_address(x.ip))
    return results
