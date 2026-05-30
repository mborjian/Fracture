import asyncio
import socket
import urllib.parse
from typing import Optional

from .traffic import _traffic


class HttpProxyServer:
    def __init__(self, host: str, port: int, interface_ipv4: str, fake_sni: bytes):
        self.host = host
        self.port = port
        self.interface_ipv4 = interface_ipv4
        self.fake_sni = fake_sni
        self.server: Optional[asyncio.Server] = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        print(f"HTTP proxy listening on {self.host}:{self.port}")

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            head_text = header.decode("iso-8859-1", errors="replace")
            request_line = head_text.split("\r\n", 1)[0]
            parts = request_line.split()
            if len(parts) < 3:
                return

            method, target, version = parts[0].upper(), parts[1], parts[2]
            if method == "CONNECT":
                host, port = self._split_host_port(target, 443)
                remote_reader, remote_writer = await self._connect(host, port)
                writer.write(f"{version} 200 Connection Established\r\n\r\n".encode("ascii"))
                await writer.drain()
            else:
                parsed = urllib.parse.urlsplit(target)
                if not parsed.hostname:
                    return
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                remote_reader, remote_writer = await self._connect(parsed.hostname, port)
                path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                lines = head_text.split("\r\n")
                lines[0] = f"{method} {path} {version}"
                forwarded = "\r\n".join(lines).encode("iso-8859-1", errors="replace")
                remote_writer.write(forwarded)
                await remote_writer.drain()

            await asyncio.gather(
                self._relay_with_count(reader, remote_writer, is_upload=True),
                self._relay_with_count(remote_reader, writer, is_upload=False),
            )
        except Exception as exc:
            print(f"HTTP proxy error: {exc}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _connect(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        from .manager import establish_connection

        loop = asyncio.get_running_loop()
        success, message, outgoing_sock = await establish_connection(
            loop, self.interface_ipv4, host, port, self.fake_sni, None
        )
        if not success or outgoing_sock is None:
            raise RuntimeError(message)
        return await asyncio.open_connection(sock=outgoing_sock)

    @staticmethod
    def _split_host_port(target: str, default_port: int) -> tuple[str, int]:
        if target.startswith("["):
            end = target.find("]")
            host = target[1:end]
            rest = target[end + 1 :]
            if rest.startswith(":"):
                return host, int(rest[1:])
            return host, default_port
        host, sep, port_raw = target.rpartition(":")
        if sep and host:
            return host, int(port_raw)
        return target, default_port

    async def _relay_with_count(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, is_upload: bool):
        try:
            while True:
                data = await reader.read(65535)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
                if is_upload:
                    _traffic.add_upload(len(data))
                else:
                    _traffic.add_download(len(data))
        except Exception:
            pass
        finally:
            writer.close()
