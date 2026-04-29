"""OKX 客户端连接池（共享 + 引用计数 + 重建）"""
import asyncio
import os
from typing import Dict, Optional, Tuple

import aiohttp
import ccxt.async_support as ccxt


# 使用系统解析器，避免 aiodns 在部分网络下的 DNS 失败
os.environ.setdefault("AIOHTTP_NO_EXTENSIONS", "1")


class OKXClientPool:
    """OKX 客户端池：同一组凭证共享连接实例"""

    _lock = asyncio.Lock()
    _clients: Dict[Tuple[str, str, str], ccxt.okx] = {}
    _refs: Dict[Tuple[str, str, str], int] = {}

    def _make_key(
        self,
        api_key: str,
        secret_key: str,
        passphrase: Optional[str],
    ) -> Tuple[str, str, str]:
        return (api_key or "", secret_key or "", passphrase or "")

    def _create_client(
        self,
        api_key: str,
        secret_key: str,
        passphrase: Optional[str],
    ) -> ccxt.okx:
        connector = aiohttp.TCPConnector(
            resolver=aiohttp.ThreadedResolver(),
            ttl_dns_cache=300,  # DNS 缓存 5 分钟
        )
        return ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret_key,
                "password": passphrase,
                "options": {
                    "defaultType": "swap",  # 永续合约
                },
                "enableRateLimit": True,
                "tcp_connector": connector,
                "timeout": 30000,  # 30秒超时，防止 API 卡住
            }
        )

    async def acquire(
        self,
        api_key: str,
        secret_key: str,
        passphrase: Optional[str],
    ) -> ccxt.okx:
        key = self._make_key(api_key, secret_key, passphrase)
        async with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = self._create_client(api_key, secret_key, passphrase)
                self._clients[key] = client
                self._refs[key] = 0
            self._refs[key] += 1
            return client

    async def release(
        self,
        api_key: str,
        secret_key: str,
        passphrase: Optional[str],
    ) -> None:
        key = self._make_key(api_key, secret_key, passphrase)
        async with self._lock:
            if key not in self._clients:
                return
            self._refs[key] -= 1
            if self._refs[key] <= 0:
                client = self._clients.pop(key)
                self._refs.pop(key, None)
                await client.close()

    async def reset(
        self,
        api_key: str,
        secret_key: str,
        passphrase: Optional[str],
    ) -> ccxt.okx:
        """强制重建连接（保留引用计数）"""
        key = self._make_key(api_key, secret_key, passphrase)
        async with self._lock:
            old = self._clients.get(key)
            if old is not None:
                await old.close()
            client = self._create_client(api_key, secret_key, passphrase)
            self._clients[key] = client
            self._refs.setdefault(key, 0)
            return client
