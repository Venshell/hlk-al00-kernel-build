#!/usr/bin/env python3
"""
fix_relative_includes.py —— 修复华为内核的相对路径 include（v4，多候选解析）

问题（实测）：
  华为内核源码大量使用带前导 `./` `../` 的 include，clang 下无法解析：
    security/hw_root_scan/./include/kcode.h:22:10: fatal error:
      './include/hw_rscan_utils.h' file not found
    kernel/fork.c:116:10: error:
      './cgroup_huawei/cgroup_pids.h' file not found with <angled> include

  GCC 在特定 -I 配置下可解析，但 clang（尤其配合 LTO）下失败。

语义分析（实测发现**不统一**）：
  样本 A: security/hw_root_scan/include/kcode.h  "./include/hw_rscan_utils.h"
    正确 = security/hw_root_scan/include/hw_rscan_utils.h
    ⟹ 相当于 **相对上两级目录**

  样本 B: kernel/hwcfs/hwcfs_common.c  <../kernel/sched/sched.h>
    正确 = kernel/sched/sched.h
    ⟹ 相当于 **相对源码根**

  ⟹ 语义不统一，无法用单条规则覆盖。

解决方案：**多候选逐一尝试，命中即用**
  候选顺序：
    1. 相对源码根：strip 全部前导 ./ ../ → 直接拼 root
    2. 标准 POSIX：os.path.normpath(join(dirname(src), inc))
    3. 上 N 级：按 inc 中 ../ 的个数从 dirname(src) 向上，再拼去掉 ../ 的尾部
    4. 逐级向上试探：对 inc 的尾段，从 dirname(src) 起逐级向上查找

  只改写**能命中**的；不能命中的保持原样（源码残留引用，模块通常未启用）。

跳过目录：
  tools/ samples/ Documentation/ scripts/ .git/

用法：
  python3 fix_relative_includes.py <kernel_root> [--dry-run] [--verbose]
"""
import os
import re
import sys

RE_ANGLE = re.compile(r'^(?P<indent>\s*)#\s*include\s*<(?P<path>\.\.?/[^>]+)>(?P<tail>.*)$')
RE_QUOTE = re.compile(r'^(?P<indent>\s*)#\s*include\s*"(?P<path>\.\.?/[^"]+)"(?P<tail>.*)$')

SKIP_DIRS = ('/tools/', '/samples/', '/Documentation/', '/scripts/', '/.git/')


def strip_prefix(p):
    """去掉全部前导 ./ 与 ../ 序列。"""
    while p.startswith('./') or p.startswith('../'):
        p = p[3:] if p.startswith('../') else p[2:]
    return p


def candidates(kernel_root, src_file, inc):
    """生成候选的绝对路径列表（按优先级）。"""
    src_dir = os.path.dirname(os.path.abspath(src_file))
    root = os.path.abspath(kernel_root)
    cands = []

    # 1) 相对源码根（strip 前缀）
    cands.append(os.path.join(root, strip_prefix(inc)))

    # 2) 标准 POSIX
    cands.append(os.path.normpath(os.path.join(src_dir, inc)))

    # 3) 逐级向上：对 inc 的尾段（去掉所有 ../ 后），从 src_dir 起向上找
    tail = strip_prefix(inc)
    d = src_dir
    for _ in range(8):
        cands.append(os.path.join(d, tail))
        parent = os.path.dirname(d)
        if parent == d or not parent.startswith(root):
            break
        d = parent

    # 去重保序
    seen = set()
    out = []
    for c in cands:
        c = os.path.normpath(c)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def fix_file(kernel_root, path, dry_run=False, verbose=False):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return 0, 0

    changed = skipped = 0
    out = []
    for line in lines:
        m = RE_ANGLE.match(line.rstrip('\n')) or RE_QUOTE.match(line.rstrip('\n'))
        if not m:
            out.append(line)
            continue

        inc = m.group('path')
        hit = None
        for c in candidates(kernel_root, path, inc):
            if os.path.isfile(c):
                root = os.path.abspath(kernel_root)
                hit = os.path.relpath(c, root).replace(os.sep, '/')
                break

        if hit is None:
            out.append(line)
            skipped += 1
            continue

        new_line = f'{m.group("indent")}#include <{hit}>{m.group("tail")}\n'
        if verbose:
            print(f'    {inc}  ->  <{hit}>')
        out.append(line if dry_run else new_line)
        changed += 1

    if changed and not dry_run:
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(out)
    return changed, skipped


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    root = os.path.abspath(sys.argv[1])
    dry = '--dry-run' in sys.argv
    verbose = '--verbose' in sys.argv

    total_c = total_s = files = 0
    skip_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        if any(x in dirpath + '/' for x in SKIP_DIRS):
            continue
        for fn in filenames:
            if not fn.endswith(('.c', '.h', '.S')):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, 'r', encoding='utf-8', errors='replace') as f:
                    head = f.read()
            except Exception:
                continue
            if ('#include "./' not in head and '#include "../' not in head
                    and '#include <./' not in head and '#include <../' not in head):
                continue
            c, s = fix_file(root, p, dry, verbose)
            if c or s:
                files += 1
                total_c += c
                total_s += s
                rel = os.path.relpath(p, root)
                print(f'  {"[dry] " if dry else ""}fix={c:2d} skip={s:2d}  {rel}')
                if s:
                    skip_files.append((rel, s))

    print()
    print(f'{"（dry-run）" if dry else ""}修复 {total_c} 处，跳过 {total_s} 处，涉及 {files} 个文件')
    if skip_files:
        print()
        print('含跳过项的文件：')
        for rel, s in skip_files[:25]:
            print(f'  skip={s:2d}  {rel}')


if __name__ == '__main__':
    main()
