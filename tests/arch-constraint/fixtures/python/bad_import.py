"""Fixture: запрещённый импорт из соседнего модуля платформы.

Этот файл сам по себе не валиден — используется только runner'ом
скрипта `check-arch-constraint.sh` для проверки detection'а.
"""

# Нарушение 1: from rehome_kb_platform.X import Y
from rehome_kb_platform.api.articles import Article

# Нарушение 2: import kb_search (без `from`)
import kb_search

__all__ = ["Article", "kb_search"]
