"""Клиент rehome.one (инструмент `platform.get_context`, §8): read-only контекст
пользователя (User/Premises/Booking) без денег (G1)."""

from __future__ import annotations

from api.clients.platform.adapter import HttpPlatformClient
from api.clients.platform.models import Booking, Premises, UserContext
from api.clients.platform.protocol import PlatformClient

__all__ = ["Booking", "HttpPlatformClient", "PlatformClient", "Premises", "UserContext"]
