# Windows 安装包

安装包：`D:\POLYMARKET\outputs\Poly5_Setup_3.1.0-dev.5_x64.exe`。

中文安装向导，默认安装到 `%LOCALAPPDATA%\Programs\POLY5`，这是当前用户的标准程序目录；可以更改目录。以当前 Windows 用户运行，不要求管理员权限。数据仍保存在 `D:\POLYMARKET\data`，安装器拒绝将程序放入数据目录。

安装会注册 Windows 卸载入口，并在程序目录创建“启动 POLY5.lnk”。桌面快捷方式为可选项，默认不选。安装末页可以直接打开程序。不会设置开机自启。

检测旧安装后提供“更新现有安装”和“保持当前版本，退出安装”选项；升级保留旧目录及数据，同版本可修复，拒绝降级。若目标程序仍在运行，安装程序会拒绝覆盖，需先通过托盘菜单“完全退出”。

卸载使用程序目录 `Uninstall.exe` 或 Windows 的“已安装的应用”。卸载按打包清单逐个删除文件，不递归清空安装目录；`D:\POLYMARKET\data` 的行情、设置与模型建议保留。

静默新安装参数示例：`Poly5_Setup_3.1.0-dev.5_x64.exe /S /D=D:\POLYMARKET\App`。`/D=` 必须放在命令行最后。已有安装的升级保留旧目录。静默卸载：`Uninstall.exe /S`。

## 重建

先用 `scripts/build.ps1` 构建桌面程序，再运行 `D:\POLYMARKET\.venv\Scripts\python.exe scripts/build_installer.py`。打包器位于 `D:\POLYMARKET\work\installer-tools\nsis-3.13`，当前编译中间产物位于 `D:\POLYMARKET\work\installer-build-dev4`（沿用历史目录名）。

编译器从 [NSIS 官方下载页](https://nsis.sourceforge.io/Download) 指向的 SourceForge 项目取得，3.13 便携 ZIP 的 SHA-256：`ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8`。

安装清单记录每个程序文件的 SHA-256。当前验证脚本 `scripts/verify_installer_dev4.py` 使用隔离的 D 盘目录和 QA 注册项（文件名沿用历史版本），实测 dev.3 升级至 dev.5、保留旧版、同版修复、降级拒绝、运行中保护、卸载和用户文件保留。报告归档到 `D:\POLYMARKET\outputs\validation\dev5`；不改真实安装注册项。

当前应用为 3.1.0-dev.5，支持自动 PAPER；功能范围与限制见 `DEVELOPMENT_REPORT_DEV4.md`。更新接口见 `UPDATES.md`，尚无远程自动更新服务或签名发布链。真实下单未实现。
