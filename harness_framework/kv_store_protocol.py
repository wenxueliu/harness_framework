"""
KVStore Protocol — 定义 ConsulClient 和 LocalStore 共用的接口

使用 typing.Protocol 实现结构化类型（PEP 544），
任何具有这 5 个方法的对象都可以作为 KVStore 使用，
无需 ABC 继承或显式注册。
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class KVStore(Protocol):
    def kv_get(self, key: str, recurse: bool = False
               ) -> tuple[Optional[Any], int]: ...
    def kv_put(self, key: str, value: str,
               cas: Optional[int] = None) -> bool: ...
    def kv_delete(self, key: str, recurse: bool = False) -> None: ...
    def kv_blocking_get(self, key: str, index: int = 0,
                        wait: str = "30s", recurse: bool = False
                        ) -> tuple[Optional[Any], int]: ...
    def list_services(self, service_name: str = "agent-worker"
                      ) -> list[dict]: ...
