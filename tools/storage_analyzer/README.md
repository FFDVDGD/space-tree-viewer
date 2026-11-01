# Storage Analyzer 报告导出

读取 Synology NAS 上已完成的 Storage Analyzer 报告，转换为查看器可读取的 JSON.GZ。计量口径为**目录级逻辑大小**，保留各级目录，直属文件归入「本目录文件（汇总）」。

## 快速开始

先在 NAS 上运行 Storage Analyzer，等待报告完成。在连接 NAS 的 Windows 电脑上，用 **PowerShell 7** 进入仓库根目录，执行：

```powershell
.\tools\storage_analyzer\Export-StorageAnalyzer.ps1 -Nas user@nas
```

把 `user@nas` 换成自己的 SSH 目标，也可填写 IP 地址或 SSH 配置别名。默认从 NAS 配置中定位报告保存位置，选择最新完成的报告，无需重新扫描。结果 `NAS名称-报告时间-logical.json.gz` 保存到本机「下载」文件夹，遵循 Windows 的文件夹重定向设置。

将结果拖入仓库的 `index.html` 或 [在线查看器](https://ffdvdgd.github.io/space-tree-viewer/)；解析在浏览器内完成，数据不上传到 GitHub。导出文件请保存在仓库外，勿提交私人报告。

## 依赖

- 电脑：Windows、PowerShell 7、PATH 中的 OpenSSH 客户端，以及现有的 SSH 公钥登录和已信任的主机密钥。
- NAS：Python 3.8+、已安装的 Storage Analyzer、已完成的报告和报告读取权限。
- 保持 `Export-StorageAnalyzer.ps1` 和 `export_storage_analyzer.py` 在同一目录。脚本按自身位置读取 Python 源码，不依赖当前工作目录。

## 参数

| 参数 | 含义 |
| --- | --- |
| `-Nas user@nas` | 必填，SSH 用户和主机、IP 地址或 SSH 配置别名 |
| `-Profile '任务名'` | 选择分析任务；有多个任务时须指定 |
| `-ReportPath '/volume1/共享/报告目录/synoreport/任务名/时间戳'` | 读取历史报告；也可传入任务目录，选择其最新完成报告 |
| `-OutputPath 'D:\reports\nas.json.gz'` | 指定本机新文件，父目录须存在 |

`-Profile` 与 `-ReportPath` 不能同时使用。同一份报告默认输出同名文件，重复导出时须指定另一个文件名或自行移走旧文件；脚本拒绝覆盖已有文件。

查看完整入口帮助：`Get-Help .\tools\storage_analyzer\Export-StorageAnalyzer.ps1 -Full`。

## 数据口径

- `value` 和 `logical` 均使用报告中的逻辑大小（文件字节数），不代表磁盘分配空间。
- 保留各级目录，目录直属文件归入「本目录文件（汇总）」，不提供逐文件明细；零字节文件仍保留数量。
- 多卷分为多个数据集，不混入文件系统空闲容量。
- 完成检查包括 `INFO` 的 `consumed_time`、`report.html` 和 `folder.db`。读取期间数据库变化、父子统计不一致或版本不支持时，导出报错。

## 安全与兼容性

Python 源码通过 SSH 标准输入执行，NAS 上不保存脚本或导出文件；只读打开 `folder.db`，在本机校验 SHA-256 后保存结果。工具不修改 SSH 配置，不申请管理员权限。

已在 DSM 7.1.1、Storage Analyzer 2.1.0-0421、ARMv7、Python 3.8.12 上验证。读取使用套件自带的 `libsynoreport.so` 解码原生 Berkeley DB；其他套件版本和硬件平台尚未实机验证。库接口参考 [Synology SDK 头文件](https://github.com/slowfranklin/synology-ds/tree/5a6dc5e1cfde5be3104f412e5a368bc8d615dfa6/usr/local/x86_64-pc-linux-gnu/x86_64-pc-linux-gnu/sys-root/usr/include/synocore)。

若自行将 `export_storage_analyzer.py` 放到 NAS 上，也可在 NAS 执行 `python3 export_storage_analyzer.py --output /自己的目录/新文件.json.gz`；可附加 `--profile` 或 `--report`，`--help` 查看说明。只需复制该 Python 文件。

## 测试

在仓库根目录运行 `python -B -m unittest discover -s tests -v`。测试使用合成报告，无需连接 NAS，不含私人目录或文件清单。
