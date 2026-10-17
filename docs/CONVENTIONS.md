# 本仓库的构建约定

## 目录

```
.github/workflows/build.yml   云编译工作流
scripts/build_kernel.sh       核心构建脚本（固化全部修复）
scripts/fix_*.py              源码自动修复工具
README.md                     背景与踩坑记录
```

## 铁律

1. **工具链版本必须匹配设备编译串**（`5484270 based on r353983c`）
   —— 不匹配则产物与真机不同，偏移不可用
2. **配置用 defconfig，不用 `/proc/config.gz`**
   —— 后者被厂商裁剪，缺 `CONFIG_CMA` 会直接编译失败
3. **偏移必须绑定"构建指纹"**（构建串 + 固件版本 + vmlinux 哈希 + config 哈希）
4. **产物只用于自有设备的授权研究**
