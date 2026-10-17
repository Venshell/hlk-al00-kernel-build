#!/usr/bin/env python3
"""
fix_extra_cflags.py —— 安全版：只改 EXTRA_CFLAGS / ccflags 行中的相对 -I（v2）

v1 的缺陷（实测发现）：
  正则 `-I[A-Za-z0-9_.]` 会误伤普通文本：
    `# SPDX-License-Identifier: GPL-2.0`
    → `# SPDX-License-I$(srctree)/dentifier: GPL-2.0`   ❌
  共误伤 566 个 Makefile（含 block/ net/ 等标准目录）。

v2 修正：
  1. **只处理赋值行**：行必须以 `EXTRA_CFLAGS`/`ccflags-y`/`ccflags-$(...)`/`CFLAGS_<obj>` 开头
  2. **只处理 -I 之后紧跟的内容**：用 `(?<=\s-I)` 或行内 `-I` 且前一字符为空白/制表
  3. **排除** `-I$(src)` / `-I$(obj)` / `-I$(srctree)` / 绝对路径 / `$` 开头
  4. 跳过 `Documentation/ tools/ samples/ scripts/` 与 `.git`

用法：
  python3 fix_extra_cflags.py <kernel_root> [--dry-run]
"""
import os
import re
import sys

# 只在这些赋值行的行首出现的形式
LINE_PREFIX = re.compile(
    r'^\s*(EXTRA_CFLAGS|ccflags-y|ccflags-\$\([^)]+\)|CFLAGS_[A-Za-z0-9_.\-]+)\s*[:+]?='
)

# 匹配 -I 后跟"相对路径"（不以 $ / 单独 / 开头）
RE_I = re.compile(r'(?P<pre>(?<=\s)|^)(?P<flag>-I)(?P<path>(?![\$/])[A-Za-z0-9_][^\s\\]*)')

SKIP_DIRS = ('/.git/', '/tools/', '/samples/', '/Documentation/', '/scripts/')


def fix_makefile(path, dry_run=False):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return 0

    changed = 0
    out = []
    for line in lines:
        body = line.rstrip('\n')
        # 只在赋值行内处理
        if not LINE_PREFIX.match(body):
            out.append(line)
            continue

        def repl(m):
            nonlocal changed
            p = m.group('path')
            if p.startswith('$(') or p.startswith('/'):
                return m.group(0)
            if '$' in p:
                return m.group(0)
            changed += 1
            return m.group('pre') + '-I$(srctree)/' + p

        new_body = RE_I.sub(repl, body)
        if new_body != body and not dry_run:
            out.append(new_body + '\n')
        else:
            out.append(line)

    if changed and not dry_run:
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(out)
    return changed


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    root = os.path.abspath(sys.argv[1])
    dry = '--dry-run' in sys.argv

    total = files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if any(x in dirpath + '/' for x in SKIP_DIRS):
            continue
        for fn in filenames:
            if fn != 'Makefile':
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, 'r', encoding='utf-8', errors='replace') as f:
                    head = f.read()
            except Exception:
                continue
            if 'EXTRA_CFLAGS' not in head and 'ccflags' not in head:
                continue
            n = fix_makefile(p, dry)
            if n:
                total += n
                files += 1
                print(f'  {"[dry] " if dry else ""}{n:2d}  {os.path.relpath(p, root)}')

    print()
    print(f'{"（dry-run）" if dry else ""}共转换 {total} 处 -I，涉及 {files} 个 Makefile')


if __name__ == '__main__':
    main()
