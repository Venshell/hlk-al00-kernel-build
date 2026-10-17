# HLK-AL00 内核云编译（CVE-2026-43499 研究用）

## 这个仓库做什么

在 GitHub Actions 上编译与**真机 HLK-AL00 完全匹配**的 `vmlinux.elf`，
用于反汇编读取内核结构体偏移（`task_struct.pi_lock` 等）。

**为什么需要**：真机内核与手上基线镜像不是同一构建，偏移不可跨版本复用；
而设备 `boot` 分区仅 root 可读、`/proc/kcore` 不存在，只能靠**重编译**得到匹配镜像。

## 真机指纹（编译目标）

```
内核:      Linux version 4.14.116 (HarmonyOS@localhost) #1 SMP PREEMPT
           Wed Sep 14 12:41:43 CST 2022
架构:      aarch64 (Cortex-A55 0xd05, Kirin 810)
设备:      HLK-AL00 / HWHLK-H / ro.board.platform=orlando
固件:      102.0.0.270C00  (2.0.0.270(C00E230R5P2))
SDK:       29  (Android 10)
```

**工具链必须精确匹配**（取自真机 `/proc/version`）：

```
Android (5484270 based on r353983c) clang version 9.0.3
  (https://android.googlesource.com/toolchain/clang 745b335211bb9eadfa6aa6301f84715cee4b37c5)
  (https://android.googlesource.com/toolchain/llvm  60cf23e54e46c807513f7a36d0a7b777920b5881)
  (based on LLVM 9.0.3svn)
```

## 使用方法

### 1. 上传源码到 Release

源码不进 git 历史（560 MB）。

```bash
# 创建 Release
gh release create source-v1 --repo <owner>/<repo> \
  --title "HLK-AL00 开源源码" \
  --notes "华为官方 4.14.116 开源包"

# 上传
gh release upload source-v1 \
  HLK-AL00-AL00a_02_HM_opensource.tar.gz \
  --repo <owner>/<repo>
```

### 2. 触发构建

```bash
gh workflow run build.yml --repo <owner>/<repo>
gh run watch --repo <owner>/<repo>
```

### 3. 取产物

```bash
gh run download --repo <owner>/<repo> --name hlk-vmlinux
```

## 构建里踩过的坑（已全部固化进 `scripts/build_kernel.sh`）

| # | 问题 | 修复 |
|---|---|---|
| 1 | `Makefile` 硬编码 `clang` 路径 `$(srctree)/../../prebuilts/clang/host/linux-x86/clang-r353983c/` | 按该路径放置工具链 |
| 2 | `CONFIG_LTO_CLANG` 要求 GNU gold ≥ 1.12；Ubuntu 已移除交叉 gold | 用 Android `aarch64-linux-android-4.9` 自带的 `ld.gold`（恰为 **1.12**） |
| 3 | 源码大量 `<./x>` `<../x>` `"./x"` 形式 include，clang 下解析失败 | `fix_relative_includes.py`（多候选路径解析） |
| 4 | 模块 `Makefile` 的 `EXTRA_CFLAGS += -Irel` 失效 | 转 `ccflags-y` 且相对路径转绝对 |
| 5 | 子目录编译**不继承**父目录 `ccflags-y` | `fix_subdir_selfpath.py` 补自身目录 |
| 6 | `LINUXINCLUDE` 缺 `-I$(srctree)` ⟹ `<drivers/...>` 解析失败 | Makefile 追加 |
| 7 | `mm/hisi/Makefile` 只在 `CONFIG_HYPERHOLD` 时加 `-I` | 改 `#include "slowpath_count.h"` |
| 8 | `srctree = "."`（相对），子目录编译时路径失效 | `$(srctree)` → 绝对路径 |
| 9 | LTO 链接需 8+ GB 内存，本地 WSL(9 GB) OOM | 用 runner（16 GB） |

## 配置说明

用 `arch/arm64/configs/merge_orlando_defconfig`，**不用**设备 `/proc/config.gz`：

> 设备 `config.gz` 显示 `# CONFIG_CMA is not set`，但源码 `gfp.h:307` 无条件引用
> `MIGRATE_CMA`（该枚举在 `#ifdef CONFIG_CMA` 内）⟹ 编译失败。
> 说明设备 config 被厂商裁剪过，**不能作为编译输入**，只能作为"构建特征"参考。

`merge_orlando_defconfig` 的关键项与设备实测一致：

| 开关 | defconfig | 设备实测 |
|---|---|---|
| `CONFIG_DEBUG_SPINLOCK` | `=y` | `=y` |
| `CONFIG_SCHEDSTATS` | `=y` | `=y` |
| `CONFIG_SCHED_HWSTATUS` | `=y` | `=y` |
| `CONFIG_RANDOMIZE_BASE` | `=y` | `=y` |
| `CONFIG_CFI_CLANG` | `=y` | `=y` |
| `CONFIG_LTO_CLANG` | `=y` | `=y` |

## 产出

| 文件 | 用途 |
|---|---|
| `vmlinux` | **反汇编取偏移**（ELF64/AArch64，含符号表） |
| `System.map` | 符号地址 |
| `kernel.config.txt` | 本次构建的完整配置（用于绑定"构建指纹"） |

## 用途声明

仅用于**自有设备**的授权安全研究（CVE-2026-43499 漏洞链分析）。
