import asyncio
import socket
import struct
from typing import Optional

from .traffic import _traffic


class Socks5Server:
    def __init__(self, host: str, port: int, interface_ipv4: str, fake_sni: bytes):
        self.host = host
        self.port = port
        self.interface_ipv4 = interface_ipv4
        self.fake_sni = fake_sni
        self.server: Optional[asyncio.Server] = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        print(f"SOCKS5 proxy listening on {self.host}:{self.port}")

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            # SOCKS5 handshake
            data = await reader.read(2)
            if len(data) < 2 or data[0] != 0x05:
                return
            n_methods = data[1]
            await reader.read(n_methods)
            writer.write(b"\x05\x00")
            await writer.drain()

            # Read request
            req = await reader.read(4)
            if len(req) < 4 or req[0] != 0x05 or req[1] != 0x01:
                return
            addr_type = req[3]
            if addr_type == 0x01:  # IPv4
                addr_data = await reader.read(4)
                port_data = await reader.read(2)
                remote_host = socket.inet_ntoa(addr_data)
                remote_port = struct.unpack(">H", port_data)[0]
            elif addr_type == 0x03:  # Domain name
                domain_len = (await reader.read(1))[0]
                domain = await reader.read(domain_len)
                remote_host = domain.decode()
                port_data = await reader.read(2)
                remote_port = struct.unpack(">H", port_data)[0]
            else:
                return

            # Lazy import avoids a startup-time circular import with manager.py.
            from .manager import establish_connection

            # Establish connection to the fixed tcp-inject upstream target.
            loop = asyncio.get_running_loop()
            success, _msg, outgoing_sock = await establish_connection(
                loop, self.interface_ipv4, None, None, self.fake_sni, None
            )
            if not success or outgoing_sock is None:
                writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
                return

            remote_reader, remote_writer = await asyncio.open_connection(sock=outgoing_sock)
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()

            # Bidirectional relay with traffic counting
            await asyncio.gather(
                self._relay_with_count(reader, remote_writer, is_upload=True),
                self._relay_with_count(remote_reader, writer, is_upload=False)
            )
        except Exception as e:
            print(f"SOCKS5 error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

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
        except:
            pass
        finally:
            writer.close()
