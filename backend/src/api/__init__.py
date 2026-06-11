"""kb-concierge backend — модуль обработки партнёрских заявок reHome.

Архитектурная константа: kb-concierge — отдельный сервис со своей БД и
кодовой базой. Никаких импортов из rehome-kb-platform / kb-support; связь
с реестром партнёров (Collaborator), заказами (ServiceOrder) и платёжным
контуром — только по HTTP API. См. CLAUDE.md правило 7 и ADR-0001.
"""

__version__ = "0.1.0"
