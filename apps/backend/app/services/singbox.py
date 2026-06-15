from __future__ import annotations

import base64
import contextlib
import dataclasses
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.services.curl_socks import measure_download_via_socks5, probe_latency_via_socks5

DEFAULT_SPEED_URL = "https://cachefly.cachefly.net/1mb.test"
DEFAULT_PROXY_PORT = 2081
DEFAULT_HTTP_PORT = 2080
DEFAULT_TUN_NAME = "fracture-singbox"
USER_AGENT = "fracture/0.1"
WARM_INSTANCE_TTL_SECONDS = 20.0
WARM_INSTANCE_LIMIT = 6


def b64decode_any(value: str) -> str:
    value = value.strip()
    padding = (-len(value)) % 4
    value += "=" * padding
    return base64.urlsafe_b64decode(value.encode("utf-8")).decode("utf-8")


def parse_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def parse_query_values(url: urllib.parse.SplitResult) -> dict[str, str]:
    raw = urllib.parse.parse_qs(url.query, keep_blank_values=True)
    return {key: values[-1] for key, values in raw.items()}


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def first_csv(value: str | None) -> str:
    values = split_csv(value)
    return values[0] if values else ""


def first_host(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return first_csv(str(value))


def host_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return split_csv(str(value))


def normalize_transport_network(value: str | None) -> str:
    network = (value or "").strip().lower()
    return "httpupgrade" if network == "xhttp" else network


def is_ip_literal(value: str) -> bool:
    with contextlib.suppress(ValueError):
        ip_address(value)
        return True
    return False


def without_none(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, inner in value.items():
            normalized = without_none(inner)
            if normalized is not None:
                cleaned[key] = normalized
        return cleaned
    if isinstance(value, list):
        return [without_none(item) for item in value if item is not None]
    return value


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Profile:
    scheme: str
    name: str
    server: str
    port: int
    uuid_or_password: str = ""
    username: str = ""
    tls: str = ""
    network: str = ""
    sni: str = ""
    alpn: list[str] = field(default_factory=list)
    allow_insecure: bool | None = None
    fingerprint: str = ""
    reality_public_key: str = ""
    reality_short_id: str = ""
    reality_spider_x: str = ""
    remark: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.remark:
            return self.remark
        return f"{self.scheme}://{self.server}:{self.port}"


def parse_profile(uri: str) -> Profile:
    uri = uri.strip()
    if not uri:
        raise ValueError("Empty profile")

    if uri.startswith("vmess://"):
        return parse_vmess(uri)
    if uri.startswith("vless://"):
        return parse_vless(uri)
    if uri.startswith("trojan://"):
        return parse_trojan(uri)
    if uri.startswith("ss://"):
        return parse_shadowsocks(uri)
    if uri.startswith("socks://") or uri.startswith("socks5://") or uri.startswith("socks4://"):
        return parse_socks(uri)
    if uri.startswith("http://") or uri.startswith("https://"):
        parsed = urllib.parse.urlsplit(uri)
        if parsed.username or "@" in parsed.netloc:
            return parse_http_proxy(uri)
    if uri.startswith("hysteria2://") or uri.startswith("hy2://"):
        return parse_hysteria2(uri)
    if uri.startswith("tuic://"):
        return parse_tuic(uri)
    if uri.startswith("wireguard://"):
        return parse_wireguard(uri)
    if uri.startswith("naive+https://") or uri.startswith("naive+quic://"):
        return parse_naive(uri)
    if uri.startswith("anytls://"):
        return parse_anytls(uri)

    raise ValueError(f"Unsupported profile scheme: {uri.split(':', 1)[0]}")


def parse_vmess(uri: str) -> Profile:
    payload = uri[len("vmess://") :]
    if "@" in payload:
        return parse_vmess_std(uri)

    data = json.loads(b64decode_any(payload))
    extras = {
        "alter_id": int(data.get("aid", 0) or 0),
        "security": data.get("scy") or "auto",
        "type": data.get("type") or "",
        "host": data.get("host") or "",
        "path": data.get("path") or "",
    }
    return Profile(
        scheme="vmess",
        name=data.get("ps") or "",
        remark=data.get("ps") or "",
        server=data["add"],
        port=int(data["port"]),
        uuid_or_password=data["id"],
        tls=data.get("tls") or "",
        network="http" if data.get("net") == "tcp" else (data.get("net") or ""),
        sni=data.get("sni") or "",
        alpn=split_csv(data.get("alpn")),
        allow_insecure=parse_bool(data.get("insecure")),
        fingerprint=data.get("fp") or "",
        extras=extras,
    )


def parse_vmess_std(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    query = parse_query_values(parsed)
    return Profile(
        scheme="vmess",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        uuid_or_password=urllib.parse.unquote(parsed.username or ""),
        tls=query.get("security", ""),
        network=query.get("type", ""),
        sni=query.get("sni", ""),
        alpn=split_csv(query.get("alpn")),
        allow_insecure=parse_bool(query.get("allowInsecure")),
        fingerprint=query.get("fp", ""),
        extras={
            "alter_id": int(query.get("aid", 0) or 0),
            "security": query.get("encryption", "auto"),
            "host": query.get("host", ""),
            "path": query.get("path", ""),
            "type": query.get("headerType", ""),
        },
    )


def parse_vless(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    query = parse_query_values(parsed)
    return Profile(
        scheme="vless",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        uuid_or_password=urllib.parse.unquote(parsed.username or ""),
        tls=query.get("security", ""),
        network=query.get("type", ""),
        sni=query.get("sni", ""),
        alpn=split_csv(query.get("alpn")),
        allow_insecure=parse_bool(query.get("allowInsecure")),
        fingerprint=query.get("fp", ""),
        reality_public_key=query.get("pbk", ""),
        reality_short_id=query.get("sid", ""),
        reality_spider_x=query.get("spx", ""),
        extras={
            "encryption": query.get("encryption", "none"),
            "flow": query.get("flow", ""),
            "host": query.get("host", ""),
            "path": query.get("path", ""),
            "service_name": query.get("serviceName", ""),
            "mode": query.get("mode", ""),
            "header_type": query.get("headerType", ""),
        },
    )


def parse_trojan(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    query = parse_query_values(parsed)
    return Profile(
        scheme="trojan",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        uuid_or_password=urllib.parse.unquote(parsed.username or ""),
        tls=query.get("security") or "tls",
        network=query.get("type", ""),
        sni=query.get("sni", ""),
        alpn=split_csv(query.get("alpn")),
        allow_insecure=parse_bool(query.get("allowInsecure")),
        fingerprint=query.get("fp", ""),
        reality_public_key=query.get("pbk", ""),
        reality_short_id=query.get("sid", ""),
        reality_spider_x=query.get("spx", ""),
        extras={
            "flow": query.get("flow", ""),
            "host": query.get("host", ""),
            "path": query.get("path", ""),
            "service_name": query.get("serviceName", ""),
            "mode": query.get("mode", ""),
        },
    )


def parse_socks(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    username = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    if username and not password and ":" not in username:
        with contextlib.suppress(Exception):
            userpass = b64decode_any(username)
            if ":" in userpass:
                username, password = userpass.split(":", 1)

    return Profile(
        scheme="socks",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        username=username,
        uuid_or_password=password,
        extras={"version": "4" if uri.startswith("socks4://") else "5"},
    )


def parse_http_proxy(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    return Profile(
        scheme="http",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        username=urllib.parse.unquote(parsed.username or ""),
        uuid_or_password=urllib.parse.unquote(parsed.password or ""),
    )


def parse_shadowsocks(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    query = parse_query_values(parsed)
    userinfo = urllib.parse.unquote(parsed.username or "")
    if ":" in userinfo:
        method, password = userinfo.split(":", 1)
    else:
        decoded = b64decode_any(userinfo)
        method, password = decoded.split(":", 1)

    return Profile(
        scheme="shadowsocks",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        uuid_or_password=password,
        extras={
            "method": method,
            "plugin": query.get("plugin", ""),
        },
    )


def parse_hysteria2(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri.replace("hy2://", "hysteria2://", 1))
    query = parse_query_values(parsed)
    return Profile(
        scheme="hysteria2",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        uuid_or_password=urllib.parse.unquote(parsed.username or ""),
        tls="tls",
        sni=query.get("sni", ""),
        allow_insecure=parse_bool(query.get("insecure")),
        alpn=split_csv(query.get("alpn")),
        extras={
            "obfs": query.get("obfs", ""),
            "obfs_password": query.get("obfs-password", ""),
            "ports": query.get("mport", ""),
        },
    )


def parse_tuic(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    query = parse_query_values(parsed)
    username = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    if not password and ":" in username:
        username, password = username.split(":", 1)
    return Profile(
        scheme="tuic",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        username=username,
        uuid_or_password=password,
        tls="tls",
        sni=query.get("sni", ""),
        alpn=split_csv(query.get("alpn")),
        allow_insecure=parse_bool(query.get("allowInsecure")),
        fingerprint=query.get("fp", ""),
        extras={
            "congestion_control": query.get("congestion_control", ""),
        },
    )


def parse_wireguard(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    query = parse_query_values(parsed)
    return Profile(
        scheme="wireguard",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        uuid_or_password=urllib.parse.unquote(parsed.username or ""),
        extras={
            "public_key": query.get("publickey", ""),
            "preshared_key": query.get("presharedkey", ""),
            "reserved": query.get("reserved", ""),
            "local_address": query.get("address", "172.16.0.2/32"),
            "mtu": int(query.get("mtu", "1280") or 1280),
        },
    )


def parse_naive(uri: str) -> Profile:
    cleaned = uri.replace("naive+https://", "https://", 1).replace("naive+quic://", "https://", 1)
    parsed = urllib.parse.urlsplit(cleaned)
    query = parse_query_values(parsed)
    return Profile(
        scheme="naive",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        username=urllib.parse.unquote(parsed.username or ""),
        uuid_or_password=urllib.parse.unquote(parsed.password or ""),
        tls="tls",
        sni=query.get("sni", ""),
        allow_insecure=parse_bool(query.get("insecure")),
        extras={
            "quic": uri.startswith("naive+quic://"),
            "congestion_control": query.get("congestion_control", ""),
        },
    )


def parse_anytls(uri: str) -> Profile:
    parsed = urllib.parse.urlsplit(uri)
    query = parse_query_values(parsed)
    return Profile(
        scheme="anytls",
        name=urllib.parse.unquote(parsed.fragment),
        remark=urllib.parse.unquote(parsed.fragment),
        server=parsed.hostname or "",
        port=parsed.port or 0,
        uuid_or_password=urllib.parse.unquote(parsed.username or ""),
        tls="tls",
        sni=query.get("sni", ""),
        allow_insecure=parse_bool(query.get("insecure")),
    )


def profile_to_record(profile: Profile, link: str) -> dict[str, Any]:
    return {
        "id": f"p-{uuid4()}",
        "name": profile.label,
        "protocol": profile.scheme,
        "server": profile.server,
        "port": profile.port,
        "group": "default",
        "link": link,
        "params": dataclasses.asdict(profile),
        "lastPingMs": None,
        "lastPingAt": None,
        "lastSpeedMbps": None,
        "lastSpeedAt": None,
        "pingSuccessCount": 0,
        "pingFailCount": 0,
    }


def record_to_profile(record: dict[str, Any]) -> Profile:
    params = record.get("params", {})
    if isinstance(params, dict) and params.get("scheme") and params.get("server"):
        return Profile(
            scheme=str(params.get("scheme", "")),
            name=str(params.get("name", record.get("name", ""))),
            server=str(params.get("server", record.get("server", ""))),
            port=int(params.get("port", record.get("port", 0))),
            uuid_or_password=str(params.get("uuid_or_password", "")),
            username=str(params.get("username", "")),
            tls=str(params.get("tls", "")),
            network=normalize_transport_network(str(params.get("network", ""))),
            sni=str(params.get("sni", "")),
            alpn=[str(item) for item in params.get("alpn", []) if str(item)],
            allow_insecure=params.get("allow_insecure"),
            fingerprint=str(params.get("fingerprint", "")),
            reality_public_key=str(params.get("reality_public_key", "")),
            reality_short_id=str(params.get("reality_short_id", "")),
            reality_spider_x=str(params.get("reality_spider_x", "")),
            remark=str(params.get("remark", "")),
            extras=params.get("extras", {}) if isinstance(params.get("extras"), dict) else {},
        )

    link = str(record.get("link", "")).strip()
    if link:
        return parse_profile(link)

    protocol = str(record.get("protocol", "")).lower()
    legacy = record.get("params", {}) if isinstance(record.get("params"), dict) else {}
    if protocol == "vless":
        return Profile(
            scheme="vless",
            name=str(record.get("name", "")),
            remark=str(record.get("name", "")),
            server=str(record.get("server", "")),
            port=int(record.get("port", 0)),
            uuid_or_password=str(legacy.get("id", "")),
            tls=str(legacy.get("security", legacy.get("tls", ""))),
            network=str(legacy.get("type", legacy.get("net", ""))),
            sni=str(legacy.get("sni", "")),
            alpn=split_csv(str(legacy.get("alpn", ""))),
            allow_insecure=parse_bool(str(legacy.get("allowInsecure", legacy.get("insecure", "")))),
            fingerprint=str(legacy.get("fp", "")),
            reality_public_key=str(legacy.get("pbk", "")),
            reality_short_id=str(legacy.get("sid", "")),
            reality_spider_x=str(legacy.get("spx", "")),
            extras={
                "encryption": str(legacy.get("encryption", "none")),
                "flow": str(legacy.get("flow", "")),
                "host": str(legacy.get("host", "")),
                "path": str(legacy.get("path", "")),
                "service_name": str(legacy.get("serviceName", "")),
                "mode": str(legacy.get("mode", "")),
                "header_type": str(legacy.get("headerType", "")),
            },
        )
    if protocol == "vmess":
        return Profile(
            scheme="vmess",
            name=str(record.get("name", "")),
            remark=str(record.get("name", "")),
            server=str(record.get("server", "")),
            port=int(record.get("port", 0)),
            uuid_or_password=str(legacy.get("id", "")),
            tls=str(legacy.get("security", legacy.get("tls", ""))),
            network=str(legacy.get("type", legacy.get("net", ""))),
            sni=str(legacy.get("sni", "")),
            alpn=split_csv(str(legacy.get("alpn", ""))),
            allow_insecure=parse_bool(str(legacy.get("allowInsecure", legacy.get("insecure", "")))),
            fingerprint=str(legacy.get("fp", "")),
            extras={
                "alter_id": int(str(legacy.get("aid", "0") or "0")),
                "security": str(legacy.get("scy", "auto")),
                "host": str(legacy.get("host", "")),
                "path": str(legacy.get("path", "")),
                "type": str(legacy.get("type", "")),
            },
        )
    if protocol == "trojan":
        return Profile(
            scheme="trojan",
            name=str(record.get("name", "")),
            remark=str(record.get("name", "")),
            server=str(record.get("server", "")),
            port=int(record.get("port", 0)),
            uuid_or_password=str(legacy.get("password", "")),
            tls=str(legacy.get("security", legacy.get("tls", "tls"))),
            network=str(legacy.get("type", legacy.get("net", ""))),
            sni=str(legacy.get("sni", "")),
            alpn=split_csv(str(legacy.get("alpn", ""))),
            allow_insecure=parse_bool(str(legacy.get("allowInsecure", legacy.get("insecure", "")))),
            fingerprint=str(legacy.get("fp", "")),
            reality_public_key=str(legacy.get("pbk", "")),
            reality_short_id=str(legacy.get("sid", "")),
            reality_spider_x=str(legacy.get("spx", "")),
            extras={
                "flow": str(legacy.get("flow", "")),
                "host": str(legacy.get("host", "")),
                "path": str(legacy.get("path", "")),
                "service_name": str(legacy.get("serviceName", "")),
                "mode": str(legacy.get("mode", "")),
            },
        )
    if protocol in {"ss", "shadowsocks"}:
        return Profile(
            scheme="shadowsocks",
            name=str(record.get("name", "")),
            remark=str(record.get("name", "")),
            server=str(record.get("server", "")),
            port=int(record.get("port", 0)),
            uuid_or_password=str(legacy.get("password", "")),
            extras={
                "method": str(legacy.get("method", "aes-128-gcm")),
                "plugin": str(legacy.get("plugin", "")),
            },
        )

    raise ValueError(f"Unsupported stored profile protocol: {protocol or 'unknown'}")


def make_tls_block(profile: Profile) -> dict[str, Any] | None:
    if profile.scheme in {"socks", "http", "wireguard", "shadowsocks"} and profile.tls not in {"tls", "reality"}:
        return None
    if profile.tls not in {"tls", "reality"}:
        return None

    tls: dict[str, Any] = {"enabled": True}
    if profile.sni:
        tls["server_name"] = profile.sni
    if profile.allow_insecure is not None:
        tls["insecure"] = profile.allow_insecure
    if profile.alpn:
        tls["alpn"] = profile.alpn
    if profile.fingerprint:
        tls["utls"] = {"enabled": True, "fingerprint": profile.fingerprint}
    if profile.tls == "reality":
        tls["reality"] = {
            "enabled": True,
            "public_key": profile.reality_public_key,
            "short_id": profile.reality_short_id,
        }
        if profile.reality_spider_x:
            tls["reality"]["spider_x"] = profile.reality_spider_x
    return tls


def make_transport_block(profile: Profile) -> dict[str, Any] | None:
    network = normalize_transport_network(profile.network)
    host = profile.extras.get("host", "")
    path = profile.extras.get("path", "")
    if not network:
        return None
    if network == "ws":
        transport = {"type": "ws"}
        if path:
            transport["path"] = path
        if host:
            transport["headers"] = {"Host": host}
        return transport
    if network == "grpc":
        transport = {"type": "grpc"}
        if profile.extras.get("service_name"):
            transport["service_name"] = profile.extras["service_name"]
        if host:
            transport["authority"] = host
        return transport
    if network == "httpupgrade":
        transport = {"type": "httpupgrade"}
        normalized_host = first_host(host)
        if normalized_host:
            transport["host"] = normalized_host
        if path:
            transport["path"] = path
        return transport
    if network == "http":
        transport = {"type": "http"}
        normalized_hosts = host_list(host)
        if normalized_hosts:
            transport["host"] = normalized_hosts
        if path:
            transport["path"] = path
        return transport
    if network == "tcp":
        return None
    if network == "kcp":
        transport = {"type": "kcp"}
        header_type = profile.extras.get("header_type")
        if header_type:
            transport["header"] = {"type": header_type}
        if path:
            transport["seed"] = path
        return transport
    return {"type": network}


def make_outbound(profile: Profile, tag: str = "proxy") -> dict[str, Any]:
    outbound: dict[str, Any] = {
        "type": profile.scheme,
        "tag": tag,
        "server": profile.server,
        "server_port": profile.port,
    }

    if profile.scheme == "vmess":
        outbound["uuid"] = profile.uuid_or_password
        outbound["security"] = profile.extras.get("security", "auto")
        if profile.extras.get("alter_id", 0):
            outbound["alter_id"] = profile.extras["alter_id"]
    elif profile.scheme == "vless":
        outbound["uuid"] = profile.uuid_or_password
        outbound["packet_encoding"] = "xudp"
        if profile.extras.get("flow"):
            outbound["flow"] = profile.extras["flow"]
    elif profile.scheme == "trojan":
        outbound["password"] = profile.uuid_or_password
    elif profile.scheme == "socks":
        outbound["version"] = profile.extras.get("version", "5")
        if profile.username:
            outbound["username"] = profile.username
        if profile.uuid_or_password:
            outbound["password"] = profile.uuid_or_password
    elif profile.scheme == "http":
        if profile.username:
            outbound["username"] = profile.username
        if profile.uuid_or_password:
            outbound["password"] = profile.uuid_or_password
    elif profile.scheme == "shadowsocks":
        outbound["method"] = profile.extras["method"]
        outbound["password"] = profile.uuid_or_password
    elif profile.scheme == "hysteria2":
        outbound["password"] = profile.uuid_or_password
        obfs = profile.extras.get("obfs")
        if obfs == "salamander" and profile.extras.get("obfs_password"):
            outbound["obfs"] = {
                "type": "salamander",
                "password": profile.extras["obfs_password"],
            }
        ports = profile.extras.get("ports")
        if ports:
            outbound.pop("server_port", None)
            outbound["server_ports"] = [item.strip().replace("-", ":") for item in ports.split(",") if item.strip()]
    elif profile.scheme == "tuic":
        outbound["uuid"] = profile.username
        outbound["password"] = profile.uuid_or_password
        if profile.extras.get("congestion_control"):
            outbound["congestion_control"] = profile.extras["congestion_control"]
    elif profile.scheme == "wireguard":
        outbound = {
            "type": "wireguard",
            "tag": tag,
            "local_address": split_csv(profile.extras.get("local_address")),
            "private_key": profile.uuid_or_password,
            "mtu": profile.extras.get("mtu", 1280),
            "peer_public_key": profile.extras.get("public_key"),
            "pre_shared_key": profile.extras.get("preshared_key") or None,
            "reserved": [int(item) for item in split_csv(profile.extras.get("reserved"))] if profile.extras.get("reserved") else None,
            "server": profile.server,
            "server_port": profile.port,
        }
    elif profile.scheme == "naive":
        outbound["username"] = profile.username
        outbound["password"] = profile.uuid_or_password
        if profile.extras.get("quic"):
            outbound["quic"] = True
        if profile.extras.get("congestion_control"):
            outbound["quic_congestion_control"] = profile.extras["congestion_control"]
    elif profile.scheme == "anytls":
        outbound["password"] = profile.uuid_or_password
    else:
        raise ValueError(f"Unsupported outbound type: {profile.scheme}")

    tls = make_tls_block(profile)
    if tls:
        outbound["tls"] = tls
    transport = make_transport_block(profile)
    if transport:
        outbound["transport"] = transport

    return without_none(outbound)


def _dns_servers_from_routing(routing: dict[str, Any]) -> list[dict[str, Any]]:
    dns_servers = split_csv(str(routing.get("dnsServers", "")))
    if not dns_servers:
        dns_servers = ["1.1.1.1", "8.8.8.8"]
    servers: list[dict[str, Any]] = []
    for idx, server in enumerate(dns_servers):
        tag = f"dns-{idx}"
        if server.startswith("https://"):
            parsed = urllib.parse.urlsplit(server)
            if parsed.hostname:
                item: dict[str, Any] = {
                    "type": "https",
                    "tag": tag,
                    "server": parsed.hostname,
                    "path": parsed.path or "/dns-query",
                }
                if parsed.port:
                    item["server_port"] = parsed.port
                if not is_ip_literal(parsed.hostname):
                    item["domain_resolver"] = "dns-bootstrap"
                servers.append(item)
            continue
        servers.append({"type": "udp", "tag": tag, "server": server})

    doh_url = str(routing.get("dohUrl", "")).strip()
    if doh_url and not any(str(server.get("type", "")) == "https" for server in servers):
        parsed = urllib.parse.urlsplit(doh_url)
        if parsed.hostname:
            item = {
                "type": "https",
                "tag": "dns-doh",
                "server": parsed.hostname,
                "path": parsed.path or "/dns-query",
            }
            if parsed.port:
                item["server_port"] = parsed.port
            if not is_ip_literal(parsed.hostname):
                item["domain_resolver"] = "dns-bootstrap"
            servers.append(item)

    bootstrap_address = next(
        (str(server.get("server")) for server in servers if str(server.get("type")) == "udp" and isinstance(server.get("server"), str)),
        "1.1.1.1",
    )
    servers.insert(0, {"type": "udp", "tag": "dns-bootstrap", "server": bootstrap_address})
    return servers


def _route_rules_from_routing(routing: dict[str, Any]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    connect_ip = str(routing.get("connectIpException", "")).strip()
    if connect_ip:
        rules.append({"ip_cidr": [f"{connect_ip}/32"], "action": "route", "outbound": "direct"})

    bypass_domains = split_csv(str(routing.get("bypassDomains", "")))
    suffixes = [item.replace("*.", "") for item in bypass_domains if item.startswith("*.")]
    exact = [item for item in bypass_domains if item and not item.startswith("*.")]
    if suffixes or exact:
        rule: dict[str, Any] = {"action": "route", "outbound": "direct"}
        if suffixes:
            rule["domain_suffix"] = suffixes
        if exact:
            rule["domain"] = exact
        rules.append(rule)

    for line in str(routing.get("routingRules", "")).splitlines():
        if "->" not in line:
            continue
        left, _, right = line.partition("->")
        source = left.strip().lower()
        target = right.strip().lower()
        if source.startswith("geoip:"):
            if source == "geoip:private":
                if target == "block":
                    rules.append({"ip_is_private": True, "action": "reject"})
                else:
                    outbound = "direct" if target == "direct" else "proxy"
                    rules.append({"ip_is_private": True, "action": "route", "outbound": outbound})
    return rules


def build_config(
    profile: Profile,
    mode: str,
    socks_port: int,
    http_port: int,
    tun_name: str,
    routing: dict[str, Any] | None = None,
    listen_host: str = "127.0.0.1",
    clash_api_port: int | None = None,
) -> dict[str, Any]:
    routing = routing or {}
    inbounds: list[dict[str, Any]] = []
    if mode in {"proxy", "mixed"}:
        inbounds.append(
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": listen_host,
                "listen_port": http_port,
            }
        )
        inbounds.append(
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": listen_host,
                "listen_port": socks_port,
            }
        )
    elif mode == "tun":
        inbounds.append(
            {
                "type": "tun",
                "tag": "tun-in",
                "interface_name": tun_name,
                "address": ["172.19.0.1/30"],
                "auto_route": True,
                "strict_route": True,
                "stack": "gvisor",
            }
        )
        inbounds.append(
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": listen_host,
                "listen_port": http_port,
            }
        )
        inbounds.append(
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": listen_host,
                "listen_port": socks_port,
            }
        )
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    route_rules = _route_rules_from_routing(routing)
    if mode == "tun":
        route_rules.insert(0, {"inbound": "tun-in", "action": "sniff"})

    config = {
        "log": {"level": "warn"},
        "dns": {
            "servers": _dns_servers_from_routing(routing),
            "strategy": "prefer_ipv4",
        },
        "inbounds": inbounds,
        "outbounds": [
            make_outbound(profile, "proxy"),
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "auto_detect_interface": True,
            "default_domain_resolver": {
                "server": "dns-bootstrap"
            },
            "rules": route_rules,
            "final": "proxy",
        },
    }
    if clash_api_port is not None:
        config["experimental"] = {
            "clash_api": {
                "external_controller": f"127.0.0.1:{clash_api_port}",
            }
        }
    return config


@dataclass
class RunningInstance:
    process: subprocess.Popen[Any]
    config_path: Path
    workdir: Path
    socks_port: int
    http_port: int
    clash_api_port: int | None
    listen_host: str
    stdout_path: Path
    stderr_path: Path

    @property
    def readiness_host(self) -> str:
        return "127.0.0.1" if self.listen_host == "0.0.0.0" else self.listen_host

    def read_log_tail(self, path: Path, limit: int = 4000) -> str:
        if not path.exists():
            return ""
        with contextlib.suppress(Exception):
            raw = path.read_bytes()
            return raw[-limit:].decode("utf-8", errors="replace").strip()
        return ""

    def last_error_summary(self) -> str:
        stderr_tail = self.read_log_tail(self.stderr_path)
        if stderr_tail:
            return stderr_tail
        stdout_tail = self.read_log_tail(self.stdout_path)
        if stdout_tail:
            return stdout_tail
        return "no runtime logs were captured"

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        with contextlib.suppress(Exception):
            self.process.terminate()
            self.process.wait(timeout=5)
            return
        if settings.root_dir.drive:
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                self.process.wait(timeout=3)
                return
        with contextlib.suppress(Exception):
            self.process.kill()
            self.process.wait(timeout=2)


@dataclass
class WarmInstanceEntry:
    key: str
    instance: RunningInstance
    last_used_monotonic: float


_WARM_INSTANCES: dict[str, WarmInstanceEntry] = {}
_WARM_INSTANCES_LOCK = threading.Lock()


def wait_until_port(host: str, port: int, timeout: float, process: subprocess.Popen[Any] | None = None, instance: RunningInstance | None = None) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if process is not None and process.poll() is not None:
            details = instance.last_error_summary() if instance is not None else "runtime exited before opening its port"
            raise RuntimeError(f"Runtime exited before opening {host}:{port}. {details}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    if process is not None and process.poll() is not None:
        details = instance.last_error_summary() if instance is not None else "runtime exited before opening its port"
        raise RuntimeError(f"Runtime exited before opening {host}:{port}. {details}")
    details = instance.last_error_summary() if instance is not None else "no runtime logs were captured"
    raise TimeoutError(f"Port {host}:{port} did not open in time. {details}")


def make_proxy_opener(http_port: int) -> urllib.request.OpenerDirector:
    proxy_url = f"http://127.0.0.1:{http_port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
    )
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def start_profile(
    profile: Profile,
    sing_box: Path,
    mode: str,
    socks_port: int,
    http_port: int,
    tun_name: str,
    routing: dict[str, Any] | None = None,
    listen_host: str = "127.0.0.1",
    config_name: str = "config.json",
    enable_clash_api: bool = False,
    startup_timeout: float = 15.0,
) -> RunningInstance:
    workdir = Path(tempfile.mkdtemp(prefix="fracture_singbox_", dir=settings.configs_dir.as_posix()))
    config_path = workdir / config_name
    stdout_path = workdir / "stdout.log"
    stderr_path = workdir / "stderr.log"
    clash_api_port = pick_free_port() if enable_clash_api else None
    config = build_config(
        profile,
        mode,
        socks_port,
        http_port,
        tun_name,
        routing=routing,
        listen_host=listen_host,
        clash_api_port=clash_api_port,
    )
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        process = subprocess.Popen(
            [str(sing_box), "run", "-c", str(config_path)],
            stdout=stdout_handle,
            stderr=stderr_handle,
            cwd=settings.root_dir.as_posix(),
        )

    instance = RunningInstance(
        process=process,
        config_path=config_path,
        workdir=workdir,
        socks_port=socks_port,
        http_port=http_port,
        clash_api_port=clash_api_port,
        listen_host=listen_host,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    try:
        wait_until_port(instance.readiness_host, http_port, timeout=startup_timeout, process=process, instance=instance)
    except Exception:
        stop_instance(instance)
        raise
    return instance


def stop_instance(instance: RunningInstance | None) -> None:
    if instance is None:
        return
    instance.stop()
    shutil.rmtree(instance.workdir, ignore_errors=True)


def _profile_cache_key(profile: Profile, routing: dict[str, Any] | None, listen_host: str) -> str:
    payload = {
        "profile": dataclasses.asdict(profile),
        "routing": routing or {},
        "listen_host": listen_host,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _prune_warm_instances_locked() -> None:
    now = time.monotonic()
    stale_keys: list[str] = []
    for key, entry in _WARM_INSTANCES.items():
        expired = now - entry.last_used_monotonic > WARM_INSTANCE_TTL_SECONDS
        exited = entry.instance.process.poll() is not None
        if expired or exited:
            stale_keys.append(key)

    for key in stale_keys:
        entry = _WARM_INSTANCES.pop(key, None)
        if entry is not None:
            stop_instance(entry.instance)

    while len(_WARM_INSTANCES) > WARM_INSTANCE_LIMIT:
        oldest_key = min(_WARM_INSTANCES.items(), key=lambda item: item[1].last_used_monotonic)[0]
        entry = _WARM_INSTANCES.pop(oldest_key, None)
        if entry is not None:
            stop_instance(entry.instance)


def stop_all_warm_instances() -> None:
    with _WARM_INSTANCES_LOCK:
        entries = list(_WARM_INSTANCES.values())
        _WARM_INSTANCES.clear()

    for entry in entries:
        stop_instance(entry.instance)


def cleanup_stale_runtime_artifacts(max_age_seconds: float = 3600.0) -> None:
    now = time.time()
    for path in settings.configs_dir.glob("fracture_singbox_*"):
        if not path.is_dir():
            continue
        with contextlib.suppress(Exception):
            if now - path.stat().st_mtime < max_age_seconds:
                continue
            shutil.rmtree(path, ignore_errors=True)


def acquire_warm_instance(
    profile: Profile,
    sing_box: Path,
    mode: str,
    socks_port: int,
    http_port: int,
    tun_name: str,
    routing: dict[str, Any] | None = None,
    listen_host: str = "127.0.0.1",
    config_name: str = "config.json",
    enable_clash_api: bool = False,
    startup_timeout: float = 15.0,
) -> RunningInstance:
    key = _profile_cache_key(profile, routing, listen_host)
    with _WARM_INSTANCES_LOCK:
        _prune_warm_instances_locked()
        cached = _WARM_INSTANCES.get(key)
        if cached is not None and cached.instance.process.poll() is None:
            cached.last_used_monotonic = time.monotonic()
            return cached.instance

    instance = start_profile(
        profile,
        sing_box,
        mode,
        socks_port,
        http_port,
        tun_name,
        routing=routing,
        listen_host=listen_host,
        config_name=config_name,
        enable_clash_api=enable_clash_api,
        startup_timeout=startup_timeout,
    )

    with _WARM_INSTANCES_LOCK:
        _prune_warm_instances_locked()
        _WARM_INSTANCES[key] = WarmInstanceEntry(
            key=key,
            instance=instance,
            last_used_monotonic=time.monotonic(),
        )
    return instance


def _socks5_probe(host: str, port: int, target_host: str, target_port: int, timeout: float) -> float:
    started = time.perf_counter()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        response = sock.recv(2)
        if response != b"\x05\x00":
            raise RuntimeError("SOCKS5 handshake failed")

        host_bytes = target_host.encode("idna")
        if is_ip_literal(target_host):
            atyp = b"\x01"
            address = socket.inet_aton(target_host)
        else:
            atyp = b"\x03"
            address = bytes([len(host_bytes)]) + host_bytes
        request = b"\x05\x01\x00" + atyp + address + target_port.to_bytes(2, "big")
        sock.sendall(request)
        head = sock.recv(4)
        if len(head) != 4 or head[1] != 0x00:
            raise RuntimeError("SOCKS5 connect failed")
        atyp = head[3]
        if atyp == 0x01:
            remaining = 4 + 2
        elif atyp == 0x03:
            domain_length = sock.recv(1)
            if not domain_length:
                raise RuntimeError("SOCKS5 malformed response")
            remaining = int(domain_length[0]) + 2
        elif atyp == 0x04:
            remaining = 16 + 2
        else:
            raise RuntimeError("SOCKS5 unknown address type")
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise RuntimeError("SOCKS5 truncated response")
            remaining -= len(chunk)
    return (time.perf_counter() - started) * 1000.0


def test_delay(
    profile: Profile,
    sing_box: Path,
    routing: dict[str, Any] | None = None,
    timeout_s: float = 8.0,
) -> float:
    deadline = time.monotonic() + max(timeout_s, 0.5)
    socks_port = pick_free_port()
    http_port = pick_free_port()
    instance = acquire_warm_instance(
        profile,
        sing_box,
        "proxy",
        socks_port,
        http_port,
        DEFAULT_TUN_NAME,
        routing=routing,
        config_name="delay.json",
        startup_timeout=max(0.5, deadline - time.monotonic()),
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Delay test timed out after 8 seconds")
    return float(probe_latency_via_socks5("127.0.0.1", instance.socks_port, timeout_s=remaining))


def test_speed(
    profile: Profile,
    sing_box: Path,
    routing: dict[str, Any] | None = None,
    seconds: float = 8.0,
) -> float:
    deadline = time.monotonic() + max(seconds, 0.5)
    socks_port = pick_free_port()
    http_port = pick_free_port()
    instance = acquire_warm_instance(
        profile,
        sing_box,
        "proxy",
        socks_port,
        http_port,
        DEFAULT_TUN_NAME,
        routing=routing,
        config_name="speed.json",
        startup_timeout=max(0.5, deadline - time.monotonic()),
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Speed test timed out after 8 seconds")
    return float(measure_download_via_socks5("127.0.0.1", instance.socks_port, timeout_s=remaining))
