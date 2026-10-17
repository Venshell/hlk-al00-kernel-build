#!/bin/bash
# =============================================================================
# build_kernel.sh —— HLK-AL00 内核构建脚本（GitHub Actions 版）
#
# 背景：本地（WSL，9GB 内存）在 LTO 链接阶段反复 OOM，改用云编译。
#
# 本脚本固化了本地多轮调试得出的**全部必要修复**：
#   1. 工具链版本必须精确匹配设备编译串：
#        "Android (5484270 based on r353983c) clang version 9.0.3"
#        "clang 745b335211bb9eadfa6aa6301f84715cee4b37c5"
#        "llvm  60cf23e54e46c807513f7a36d0a7b777920b5881"
#        "LLVM 9.0.3svn"
#   2. Makefile 硬编码 clang 路径：
#        CLANG_PREBUILTS_PATH ?= $(srctree)/../../prebuilts/clang/host/linux-x86/clang-r353983c/
#   3. CONFIG_LTO_CLANG 要求 GNU gold >= 1.12（Ubuntu 26.04 已移除交叉 gold）
#      ⟹ 用 Android aarch64-linux-android-4.9 自带的 ld.gold（恰为 1.12）
#   4. 源码大量 <./x> / <../x> / "./x" 形式 include，clang 下无法解析
#   5. 模块 Makefile 的 EXTRA_CFLAGS 在现代 Kbuild 下失效 ⟹ 转 ccflags-y
#   6. 子目录编译不继承父目录 ccflags-y ⟹ 需补自身目录
#   7. LINUXINCLUDE 缺 -I$(srctree) ⟹ <drivers/...> 形式解析失败
#
# 用法：
#   ./build_kernel.sh <source.tar.gz> <outdir>
# =============================================================================
set -euo pipefail

SRC_TGZ="${1:?usage: build_kernel.sh <source.tar.gz> <outdir>}"
OUTDIR="${2:?usage: build_kernel.sh <source.tar.gz> <outdir>}"
WORK="${WORK:-$PWD/_work}"
ABS="$(realpath "$WORK")"

log() { echo "::group::$*"; }
endlog() { echo "::endgroup::"; }
step() { echo ""; echo "===== $* ====="; }

mkdir -p "$OUTDIR"

# -----------------------------------------------------------------------------
step "0. 环境信息"
# -----------------------------------------------------------------------------
echo "WORK      = $ABS"
echo "SRC_TGZ   = $SRC_TGZ"
echo "OUTDIR    = $OUTDIR"
echo "nproc     = $(nproc)"
echo "memory    = $(free -g | awk '/^Mem:/{print $2}') GB"
df -h "$WORK" "$OUTDIR" || true

# -----------------------------------------------------------------------------
step "1. 解压源码"
# -----------------------------------------------------------------------------
cd "$WORK"
rm -rf Code_Opensource
tar -xzf "$SRC_TGZ" --wildcards 'Code_Opensource/kernel/*'
K="$WORK/Code_Opensource/kernel"
echo "内核源码: $K"
du -sh "$K"
head -5 "$K/Makefile"

# 版本核对
grep -E '^(VERSION|PATCHLEVEL|SUBLEVEL) ' "$K/Makefile" | head -3

# -----------------------------------------------------------------------------
step "2. 获取工具链（clang-r353983c + aarch64 GCC 4.9）"
# -----------------------------------------------------------------------------
# GitHub runner 在境外，可直连 android.googlesource.com
CLANG_REPO="https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86"
GCC_REPO="https://android.googlesource.com/platform/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9"
TAG="android-10.0.0_r1"

mkdir -p "$WORK/prebuilts/clang/host/linux-x86"
mkdir -p "$WORK/toolchain"

if [ ! -d "$WORK/prebuilts/clang/host/linux-x86/clang-r353983c" ]; then
  log "clone clang"
  git clone --depth 1 --branch "$TAG" --filter=blob:none --sparse \
      "$CLANG_REPO" "$WORK/clangrepo"
  cd "$WORK/clangrepo"
  git sparse-checkout set clang-r353983c
  cp -r clang-r353983c "$WORK/prebuilts/clang/host/linux-x86/"
  cd "$WORK"
  rm -rf "$WORK/clangrepo"
  endlog
fi

if [ ! -d "$WORK/toolchain/gcc49" ]; then
  log "clone gcc 4.9"
  git clone --depth 1 --branch "$TAG" "$GCC_REPO" "$WORK/toolchain/gcc49"
  endlog
fi

CLANG="$WORK/prebuilts/clang/host/linux-x86/clang-r353983c/bin"
GCC49="$WORK/toolchain/gcc49/bin"

echo "--- clang 版本（必须与设备编译串一致）---"
"$CLANG/clang" --version | head -4

echo "--- ld.gold 版本（必须 >= 1.12）---"
"$GCC49/aarch64-linux-android-ld.gold" --version | head -2

# 校验：设备编译串关键标识
if "$CLANG/clang" --version | grep -q '5484270 based on r353983c'; then
  echo "✅ clang 标识 5484270/r353983c 匹配"
else
  echo "❌ clang 标识不匹配，无法保证与设备一致"; exit 1
fi

# -----------------------------------------------------------------------------
step "3. 源码修复"
# -----------------------------------------------------------------------------
cd "$K"
ABS="$K"

# ---- 3.1 LINUXINCLUDE 补 -I$(srctree) 与 -I$(objtree) ----
echo "--- 3.1 LINUXINCLUDE ---"
python3 - "$K/Makefile" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p, encoding='utf-8', errors='replace').read()
anchor = "\t\t   -I$(srctree)/lib/libc_sec/securec_v2/include"
add = anchor + " \\\n\t\t   -I$(srctree) \\\n\t\t   -I$(objtree)"
if "-I$(srctree) \\" in s:
    print("  已存在，跳过")
else:
    s = s.replace(anchor, add, 1)
    open(p, 'w', encoding='utf-8').write(s)
    print("  ✅ 已追加 -I$(srctree) -I$(objtree)")
PYEOF

# ---- 3.2 mm/hisi 的 <slowpath_count.h> ----
echo "--- 3.2 slowpath_count.c ---"
sed -i 's|#include <slowpath_count.h>|#include "slowpath_count.h"|' \
    mm/hisi/slowpath_count.c 2>/dev/null && echo "  已修" || echo "  (文件不存在，跳过)"

# ---- 3.3 相对 include（<./x> <../x> "./x" "../x"）----
echo "--- 3.3 相对 include ---"
cp /action/scripts/fix_relative_includes.py /tmp/ 2>/dev/null || true
python3 /action/scripts/fix_relative_includes.py "$K" 2>&1 | tail -3

# ---- 3.4 EXTRA_CFLAGS -> ccflags-y，并把相对 -I 转绝对 ----
echo "--- 3.4 Makefile cflags ---"
python3 /action/scripts/fix_extra_cflags.py "$K" 2>&1 | tail -2

# 3.4b EXTRA_CFLAGS -> ccflags-y
FIXED=0
while IFS= read -r f; do
  if grep -q '^EXTRA_CFLAGS' "$f" 2>/dev/null; then
    sed -i 's|^EXTRA_CFLAGS|ccflags-y|g' "$f"
    FIXED=$((FIXED+1))
  fi
done < <(grep -rl 'EXTRA_CFLAGS' --include='Makefile' . 2>/dev/null || true)
echo "  EXTRA_CFLAGS→ccflags-y: $FIXED 个文件"

# 3.4c $(srctree)/ -> 绝对路径（srctree 是 "."，相对路径在子目录编译时失效）
echo "--- 3.5 srctree 绝对化 ---"
N=0
while IFS= read -r f; do
  if grep -q 'ccflags-y.*\$(srctree)' "$f" 2>/dev/null; then
    sed -i '/^ccflags-y/s|\$(srctree)|'"$ABS"'|g' "$f"
    N=$((N+1))
  fi
done < <(grep -rl 'ccflags-y.*\$(srctree)' --include='Makefile' . 2>/dev/null || true)
echo "  srctree→绝对路径: $N 个文件"

# 3.5d subdir-ccflags-y 也要处理
while IFS= read -r f; do
  if grep -q 'subdir-ccflags-y.*\$(srctree)' "$f" 2>/dev/null; then
    sed -i '/^subdir-ccflags-y/s|\$(srctree)|'"$ABS"'|g' "$f"
  fi
done < <(grep -rl 'subdir-ccflags-y.*\$(srctree)' --include='Makefile' 2>/dev/null || true)

# ---- 3.6 子目录补自身路径 ----
echo "--- 3.6 子目录自身路径 ---"
python3 /action/scripts/fix_subdir_selfpath.py "$K" 2>&1 | tail -2

# 3.6b 无条件为所有含 .c 的目录补自身绝对路径
echo "--- 3.7 无条件补自身路径 ---"
# 注意：必须**追加到文件末尾**。
# 若插到开头，模块里的 `ccflags-y :=`（赋值而非追加）会把它抹掉
# （本地在 drivers/huawei_platform/hwsdp 踩过同样的坑）。
CNT=0
while IFS= read -r mf; do
  d=$(dirname "$mf")
  ls "$d"/*.c >/dev/null 2>&1 || continue
  absd="$ABS/${d#./}"
  grep -qF "$absd/" "$mf" && continue
  # 追加到末尾（换行后再写）
  { [ -n "$(tail -c1 "$mf")" ] && echo; printf 'ccflags-y += -I%s/\n' "$absd"; } >> "$mf"
  CNT=$((CNT+1))
done < <(find . -name 'Makefile' \
          -not -path './tools/*' -not -path './samples/*' \
          -not -path './Documentation/*' -not -path './scripts/*' 2>/dev/null)
echo "  补自身路径（末尾追加）: $CNT 个目录"

# -----------------------------------------------------------------------------
step "4. 配置"
# -----------------------------------------------------------------------------
cd "$K"
export ARCH=arm64 SRCARCH=arm64 SUBARCH=arm64
export CROSS_COMPILE=aarch64-linux-android-
export CROSS_COMPILE_ARM32=arm-linux-androideabi-
export CC=clang
export LD="$GCC49/aarch64-linux-android-ld.gold"
export AR="$CLANG/llvm-ar"
export NM="$CLANG/llvm-nm"
export OBJCOPY="$CLANG/llvm-objcopy"
export OBJDUMP="$CLANG/llvm-objdump"
export READELF="$CLANG/llvm-readelf"
export STRIP="$CLANG/llvm-strip"
export HOSTCC=clang
export HOSTCXX=clang++
export HOSTAR="$CLANG/llvm-ar"
export HOSTLD=ld.lld
export PATH="$CLANG:$GCC49:$PATH"

# 用 defconfig（不是 /proc/config.gz —— 后者被华为裁剪过，缺 CONFIG_CMA）
make merge_orlando_defconfig 2>&1 | grep -vE 'unsupported character' | tail -3

echo "--- 关键开关核对 ---"
grep -E '^CONFIG_(DEBUG_SPINLOCK|SCHEDSTATS|SCHED_HWSTATUS|CMA|RANDOMIZE_BASE|CFI_CLANG|LTO|KALLSYMS_ALL|MODULE_SIG)=' .config || true

# -----------------------------------------------------------------------------
step "5. prepare"
# -----------------------------------------------------------------------------
make -j"$(nproc)" prepare 2>&1 | tail -5

# -----------------------------------------------------------------------------
step "6. 构建 vmlinux"
# -----------------------------------------------------------------------------
echo "开始: $(date)"
BUILD_LOG="$WORK/build.log"
set +e
make -j"$(nproc)" vmlinux > "$BUILD_LOG" 2>&1
RC=$?
set -e
echo "结束: $(date)  exit=$RC"

# 打印错误（不截断关键信息）
if [ $RC -ne 0 ]; then
  echo "::group::编译错误"
  grep -nE 'error:|fatal error|Error [0-9]|\*\*\*|undefined reference|No such file' "$BUILD_LOG" | tail -60 || true
  echo "::endgroup::"

  echo "::group::错误上下文（首个 error 前后 20 行）"
  FIRST=$(grep -nE 'error:|fatal error' "$BUILD_LOG" | head -1 | cut -d: -f1)
  if [ -n "$FIRST" ]; then
    S=$((FIRST>20 ? FIRST-20 : 1))
    E=$((FIRST+20))
    sed -n "${S},${E}p" "$BUILD_LOG"
  else
    tail -60 "$BUILD_LOG"
  fi
  echo "::endgroup::"

  cp "$BUILD_LOG" "$OUTDIR/build.log" 2>/dev/null || true
  echo "::error::vmlinux 构建失败 (exit=$RC)"
  exit 1
fi

# 成功也保留尾部
tail -20 "$BUILD_LOG"

# -----------------------------------------------------------------------------
step "7. 产物"
# -----------------------------------------------------------------------------
ls -la vmlinux System.map 2>&1 || true

if [ ! -f vmlinux ]; then
  echo "::error::vmlinux 未生成"
  exit 1
fi

cp vmlinux "$OUTDIR/"
[ -f System.map ] && cp System.map "$OUTDIR/"
cp .config "$OUTDIR/kernel.config.txt"

echo "--- 产物 ---"
ls -la "$OUTDIR"
file "$OUTDIR/vmlinux" 2>/dev/null || "$CLANG/llvm-readelf" -h "$OUTDIR/vmlinux" | head -12

echo ""
echo "✅ 构建完成"
