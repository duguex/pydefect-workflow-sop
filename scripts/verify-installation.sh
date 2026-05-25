#!/usr/bin/env bash
#=============================================================================
# PyDefect 环境可用性验证脚本
#
# 用途: 检查 pydefect + pydefect_vasp + pydefect_util 三大 CLI 及核心依赖
#       是否完整可用的快速健康检查。
#
# 用法: source /opt/conda/etc/profile.d/conda.sh && conda activate pydefect
#       bash scripts/verify-installation.sh
#
# 返回: 0 = 全部通过, 1 = 有失败的检查项
#=============================================================================

PASS=0
FAIL=0

pass()  { echo "  ✅ $1"; ((PASS++)); }
fail()  { echo "  ❌ $1"; ((FAIL++)); return 1; }

# --- 1. CLI entry points ---
echo "--- CLI entry points ---"

for cmd in pydefect pydefect_vasp pydefect_util; do
    if command -v "$cmd" &>/dev/null; then
        pass "$cmd found at $(which "$cmd")"
    else
        fail "$cmd NOT found in PATH"
    fi
done

# --- 2. Help output (verifies subcommands load) ---
echo "--- CLI smoke test (--help) ---"

pydefect --help &>/dev/null \
  && pass "pydefect --help OK" \
  || fail "pydefect --help failed"

pydefect_vasp --help &>/dev/null \
  && pass "pydefect_vasp --help OK" \
  || fail "pydefect_vasp --help failed"

pydefect_util --help &>/dev/null \
  && pass "pydefect_util --help OK" \
  || fail "pydefect_util --help failed"

# --- 3. Python imports ---
echo "--- Python imports ---"

python -c "import pydefect; print(pydefect.__version__)" &>/dev/null \
  && pass "pydefect Python import (v$(python -c 'import pydefect; print(pydefect.__version__)' 2>/dev/null))" \
  || fail "pydefect Python import FAILED"

python -c "from pydefect.util.error_classes import NotPrimitiveError" &>/dev/null \
  && pass "NotPrimitiveError import OK" \
  || fail "NotPrimitiveError import FAILED"

for mod in pymatgen vise numpy; do
    python -c "import $mod" &>/dev/null \
      && pass "$mod import OK" \
      || fail "$mod import FAILED"
done

# --- 4. Internal module paths (for debugging) ---
echo "--- Internal module paths ---"

pydefect_vasp_entry=$(which pydefect_vasp 2>/dev/null)
if [[ -n "$pydefect_vasp_entry" ]]; then
    internal_mod=$(grep -oP 'from \K[\w.]+' "$pydefect_vasp_entry" 2>/dev/null | head -1)
    pass "pydefect_vasp → $internal_mod"
fi

pydefect_util_entry=$(which pydefect_util 2>/dev/null)
if [[ -n "$pydefect_util_entry" ]]; then
    internal_mod=$(grep -oP 'from \K[\w.]+' "$pydefect_util_entry" 2>/dev/null | head -1)
    pass "pydefect_util → $internal_mod"
fi

# --- Summary ---
echo ""
echo "=============="
echo " Passed: $PASS"
echo " Failed: $FAIL"
echo "=============="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
