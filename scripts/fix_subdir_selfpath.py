#!/usr/bin/env python3
"""
fix_subdir_selfpath.py —— 为含源文件的子目录 Makefile 补上"自身目录"的 -I

根因（实测）：
  Kbuild **不会**把父目录 Makefile 的 ccflags-y 继承给子目录编译单元。
  而华为的某些子目录 Makefile 只列了父级路径，漏了自己的：

    sound/soc/codecs/hisi/da_separate/Makefile:
      ccflags-y += -I.../da_separate/            ← 父级有这行
      ccflags-y += -I.../da_separate/da_separate_v3/   ← 也列了 v3

    sound/soc/codecs/hisi/da_separate/da_separate_v3/Makefile:
      ccflags-y += -I.../drivers/hisi/da_separate/
      ccflags-y += -I.../sound/soc/codecs/hisi/
      ccflags-y += -I.../sound/soc/codecs/hisi/da_separate/   ← 只到父级
      ccflags-y += -I.../sound/soc/codecs/hisi/adapter/
                                                   ← ❌ 缺自己的目录

  导致 da_separate_utils.h 的 `#include "da_separate_v3.h"`（同级）解析失败：
    ./sound/soc/codecs/hisi/da_separate/da_separate_utils.h:23:10:
      fatal error: 'da_separate_v3.h' file not found

修复：
  对每个**同时满足**以下条件的 Makefile：
    - 该目录内含 .c 文件（即会被编译）
    - 已有 ccflags-y（说明依赖搜索路径）
    - ccflags-y 中**不包含自身目录的绝对路径**
  则在首行插入：
    ccflags-y += -I<abs(self_dir)>/

  注意：**仅补自身目录**，不改变其他语义。

用法：
  python3 fix_subdir_selfpath.py <kernel_root> [--dry-run]
"""
import os
import sys

SKIP_DIRS = ('/.git/', '/tools/', '/samples/', '/Documentation/', '/scripts/')


def has_c_files(dirpath):
    try:
        for fn in os.listdir(dirpath):
            if fn.endswith('.c'):
                return True
    except Exception:
        pass
    return False


def fix_makefile(dirpath, abs_root, dry_run=False):
    mf = os.path.join(dirpath, 'Makefile')
    try:
        with open(mf, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return 0

    text = ''.join(lines)
    if 'ccflags-y' not in text:
        return 0

    self_abs = dirpath.replace(os.sep, '/')
    # 自身目录已列出？（用带尾斜杠的形式判断，避免前缀误判）
    needle = self_abs + '/'
    if needle in text:
        return 0

    new_line = f'ccflags-y += -I{needle}\n'
    if dry_run:
        return 1

    # 必须追加到末尾：模块里的 ccflags-y := 会覆盖前面的 += 行
    if lines and not lines[-1].endswith('n'):
        lines[-1] += 'n'
    lines.append(new_line)
    with open(mf, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    root = os.path.abspath(sys.argv[1])
    dry = '--dry-run' in sys.argv

    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if any(x in dirpath + '/' for x in SKIP_DIRS):
            continue
        if 'Makefile' not in filenames:
            continue
        if not has_c_files(dirpath):
            continue
        n = fix_makefile(dirpath, root, dry)
        if n:
            total += n
            print(f'  {"[dry] " if dry else ""}{os.path.relpath(dirpath, root)}')

    print()
    print(f'{"（dry-run）" if dry else ""}共处理 {total} 个目录')


if __name__ == '__main__':
    main()
