"""Fixture: легитимное использование с inline allowlist-комментарием.

Должно проходить проверку AT-001 благодаря `# arch-allow:` маркеру
на ТОЙ ЖЕ строке, где находится потенциальное нарушение.
"""

# Example #1: Python import detection bypass.
import kb_search  # arch-allow: fixture для unit-теста скрипта AT-001

# Example #2: SQL detection bypass на той же строке.
SAMPLE_SQL = "SELECT * FROM users"  # arch-allow: fixture для теста SQL-правила

__all__ = ["kb_search", "SAMPLE_SQL"]
