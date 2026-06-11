#!/usr/bin/env bash
# Test runner для scripts/check-arch-constraint.sh.
#
# Прогоняет скрипт на каждой fixture-папке и проверяет ожидаемый exit code.
# Запуск:
#   bash tests/arch-constraint/test_runner.sh
#
# Exit code: 0 — все кейсы прошли; 1 — есть провал.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHECK_SCRIPT="$REPO_ROOT/scripts/check-arch-constraint.sh"

if [[ ! -x "$CHECK_SCRIPT" ]]; then
    echo "FAIL: $CHECK_SCRIPT не найден или не executable"
    exit 1
fi

passed=0
failed=0

# Хелпер: запускает скрипт на одном fixture-файле, временно симулируя его
# в одной из сканируемых директорий через bind-копию.
#
# args: <fixture-relpath> <expected-exit-code> <case-name>
run_case() {
    local fixture=$1 expected=$2 name=$3
    local tmpdir
    tmpdir=$(mktemp -d)

    # Подкладываем fixture как backend/src/api/_arch_test.<ext> (или ts эквивалент).
    local ext=${fixture##*.}
    if [[ "$ext" == "py" ]]; then
        mkdir -p "$tmpdir/backend/src/api"
        cp "$SCRIPT_DIR/fixtures/$fixture" "$tmpdir/backend/src/api/_arch_test.py"
    elif [[ "$ext" == "ts" ]]; then
        mkdir -p "$tmpdir/frontend/app"
        cp "$SCRIPT_DIR/fixtures/$fixture" "$tmpdir/frontend/app/_arch_test.ts"
    else
        echo "FAIL [$name]: неизвестное расширение fixture'а $ext"
        rm -rf "$tmpdir"
        failed=$((failed + 1))
        return
    fi

    # Запускаем скрипт из tmpdir.
    local actual=0
    (cd "$tmpdir" && bash "$CHECK_SCRIPT" >/dev/null 2>&1) || actual=$?
    rm -rf "$tmpdir"

    if [[ "$actual" == "$expected" ]]; then
        printf '  PASS [%s] expected=%s actual=%s\n' "$name" "$expected" "$actual"
        passed=$((passed + 1))
    else
        printf '  FAIL [%s] expected=%s actual=%s\n' "$name" "$expected" "$actual"
        failed=$((failed + 1))
    fi
}

echo "Running AT-001 arch-constraint test cases..."

run_case python/bad_import.py 1 "python-forbidden-import"
run_case python/bad_sql.py    1 "python-forbidden-sql"
run_case python/bad_sql_lowercase.py 1 "python-forbidden-sql-lowercase"
run_case python/own_tables.py 0 "python-own-tables-ok"
run_case python/allowed.py    0 "python-allowlist"
run_case typescript/bad_import.ts 1 "ts-forbidden-import"

echo ""
echo "Summary: passed=$passed failed=$failed"

if (( failed > 0 )); then
    exit 1
fi
