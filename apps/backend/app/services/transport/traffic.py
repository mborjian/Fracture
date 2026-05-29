import asyncio
from typing import Optional, Tuple


class TrafficMonitor:
    def __init__(self):
        self.download_bytes = 0
        self.upload_bytes = 0
        self.last_reset = asyncio.get_event_loop().time()

    def add_download(self, n: int):
        self.download_bytes += n

    def add_upload(self, n: int):
        self.upload_bytes += n

    def reset(self):
        self.download_bytes = 0
        self.upload_bytes = 0
        self.last_reset = asyncio.get_event_loop().time()


_traffic = TrafficMonitor()


async def fetch_egress_via_socks5(socks_port: int) -> Tuple[Optional[str], Optional[str]]:
    import aiohttp
    from aiohttp_socks import ProxyConnector

    connector = ProxyConnector.from_url(f"socks5://127.0.0.1:{socks_port}")
    async with aiohttp.ClientSession(connector=connector) as session:
        ip = None
        country = None
        try:
            async with session.get("https://api.ipify.org?format=json", timeout=5) as resp:
                data = await resp.json()
                ip = data.get("ip")
        except:
            pass
        try:
            async with session.get("https://ipapi.co/json", timeout=5) as resp:
                data = await resp.json()
                country = data.get("country_name")
                if not ip:
                    ip = data.get("ip")
        except:
            pass
    return ip, country
