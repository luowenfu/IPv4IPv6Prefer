> **声明：** 本工具由 AI 制作。

# IPv4IPv6Prefer

Windows 双栈优先级管理工具。通过调整 IPv6 前缀策略表，在 **IPv4 优先** 与 **IPv6 优先** 之间切换，**不禁用 IPv6**。

修改策略需要管理员权限，程序启动时会请求 UAC。

<p align="center">
  <img src="assets/preview.png" alt="界面预览" width="480">
</p>

## 功能

- 读取并显示当前系统策略（`::ffff:0:0/96` 与 `::/0` 的 precedence）
- 一键设为 IPv4 优先或 IPv6 优先
- 对双栈探测站做 Ping 验证（国内站点优先，失败再试国外）

## 环境

- Windows 10 / 11
- 源码运行需要 Python 3.10+（仅用标准库，无需额外依赖）
- 打包 exe 需要 [PyInstaller](https://pyinstaller.org/)

## 下载

预编译程序：[`dist/IPv4IPv6Prefer.exe`](dist/IPv4IPv6Prefer.exe)

## 源码运行

```powershell
python src/main.py
```

## 自行打包

```powershell
.\build.ps1
```

输出：`dist\IPv4IPv6Prefer.exe`

## 原理

Windows 双栈选址遵循前缀策略表（RFC 6724）。**precedence 越大越优先**，比较这两条即可：

| 前缀 | 含义 | 默认 precedence |
|------|------|-----------------|
| `::/0` | 普通 IPv6 | 40 |
| `::ffff:0:0/96` | IPv4 映射地址 | 35 |

默认 `40 > 35`，所以走 IPv6。本工具只改映射地址的 precedence：

- **IPv4 优先**：设为 46（高于 40）
- **IPv6 优先**：改回 35

IPv6 协议栈始终开启，既不禁用网卡 IPv6，也不改 `DisabledComponents`。

Windows 把自定义策略写入持久化存储时会**整表替换**内置项。若只改一条，重启后 `::/0` 等默认行可能丢失。因此切换时会一并写入完整默认表，只调整上述 precedence。

访问验证对探测站分别 `ping -4` / `ping -6`，再无参数 ping 一次，看系统实际选了哪一边。

## 说明

- 自定义验证站点请确保为双栈站点（如 `vv.tcptest.cn`、`www.baidu.com`）。不要用 `baidu.com`（无 AAAA，结果恒为 IPv4）。
- 部分应用需重新建立连接后才会按新策略选址。
