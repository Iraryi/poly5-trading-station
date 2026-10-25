# 更新接口 v1

程序、模型和数据分别版本化。应用版本在 poly5/__init__.py；盘口模型包含 schema_version、model_id 与 features；账本事件仍为 3.1，设置仍为 1。模型随程序发布，运行中的模型、策略阈值和风险限额不热更新。

设置页“选择更新清单”通过独立进程读取本地 JSON 清单，校验产品、通道、Windows x64、版本次序、数据格式兼容性、安装包大小和 SHA-256。发现更新后由用户打开安装器。检查和打开向导都不会自动退出行情程序，不会自动安装或改变交易权限。

构建器生成 D:\POLYMARKET\outputs\update-manifest.json。清单 schema_version=1，包含 product_id、channel、platform、version、compatibility（settings_schema_min/max、readable_event_schemas）、package（同目录 EXE 文件名、bytes、sha256）、notes、restart_required。安装包文件不允许目录跳转、URL 或指向目录外的链接。SHA-256 验证完整性，不证明发布者身份。

poly5.updates.UpdateSource 是预留来源接口，目前实现 LocalUpdateSource。未来接远程发布服务时需要可信 HTTPS 来源、清单签名/发布者验证、下载暂存和验证；本版未设置远程服务、静默下载或后台强制升级。

安装器识别当前用户注册项，并核对目标目录 installation.ini 的产品 ID。已有安装显示“更新现有安装（同版本可修复）/保持当前版本”；更新保留旧目录、桌面快捷方式选择与独立用户数据。写入 BuildVersion、SettingsSchema、产品 ID 和安装清单。禁止用旧包覆盖较新版本；运行中的 EXE 不被终止或强制替换。旧版注册项来自 dev.3 时兼容解析 Version。

卸载按当前包清单删除管理文件，保留未知用户文件。没有实现事务回滚、断电后的自动恢复或自动清除历版遗留依赖；更新失败保留用户数据，可在程序退出后重新运行同版本安装包修复。未来数据迁移应先备份、逐级迁移和验证，再提升 schema_version；遇到较新设置格式，本版拒绝改写原文件。

安装器自动验证使用单独 QA 注册项，绝不覆盖用户真实安装登记。运行中的用户程序不参与退出、升级或卸载测试。
