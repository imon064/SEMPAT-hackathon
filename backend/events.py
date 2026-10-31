"""Penyiaran kejadian ke semua klien lewat Server-Sent Events (SSE)."""
from __future__ import annotations

import asyncio
import json
from typing import Optional, Set


class Broker:
    def __init__(self) -> None:
        self.clients: Set[asyncio.Queue] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def _put_all(self, msg: str) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # klien lambat: lewati; ia akan menyamakan state lewat versi

    def publish(self, event: str, data: dict) -> None:
        msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        if self.loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._put_all(msg)
        else:
            self.loop.call_soon_threadsafe(self._put_all, msg)
