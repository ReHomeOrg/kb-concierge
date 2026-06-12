"""HTTP-клиенты к соседям-инструментам «Консьержа» (§8, арх-константа ADR-0001).

Доступ к KB/kb-search, kb-support, kb-partners, rehome.one — ТОЛЬКО по сети, через
resilient-клиенты (timeout→circuit-breaker→retry→метрики, cache-aside). Никакого SQL/
импорта кода соседей. Адаптеры инструментов (search/platform/...) — в подпакетах.
"""

from __future__ import annotations
