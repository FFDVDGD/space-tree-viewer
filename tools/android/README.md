# Android Root 存储导出

通过 ADB 在手机上遍历 `/data` 的文件元数据，导出按**分配空间**计量的 JSON.GZ。默认保留至少 **50 MB（50,000,000 字节）**的文件和目录，小项目合并展示并保留统计总量。

## 快速开始

连接手机并授权 USB 调试和 ADB shell Root，在电脑上进入仓库根目录运行：

```powershell
python -B .\tools\android\export_android.py
```

每次生成新的当前快照 `android-机型-时间-allocated-50MB.json.gz`，保存到本机「下载」文件夹，遵循 Windows 文件夹重定向设置。将结果拖入仓库的 `index.html` 或 [在线查看器](https://ffdvdgd.github.io/space-tree-viewer/)；解析在浏览器内完成，数据不上传到 GitHub。导出文件请保存在仓库外，勿提交私人报告。

## 依赖

- 手机：**ARM64 Android 8.0 / API 26+**，已开启并授权 USB 调试，已授予 ADB shell Root。
- 电脑：Python 3.8+、PATH 中的 ADB、已安装的 Android NDK。不自动安装依赖。
- 自动查找 Android SDK 的 `ndk` 目录和 `ANDROID_NDK_HOME` / `ANDROID_NDK_ROOT`；也可用 `--ndk` 指定 NDK 根目录。
- 保持 `export_android.py` 和 `android_scan.c` 在同一目录。脚本按自身位置定位 C 源码，不依赖当前工作目录。

## 参数

| 参数 | 含义 |
| --- | --- |
| `--min-mb 50` | 最小独立明细，默认 50，使用十进制 MB；例如 `25` 可进一步细化 |
| `--serial SERIAL` | 选定 ADB 设备；连接多个设备时须指定 |
| `--ndk 'D:\Android\Sdk\ndk\版本'` | 显式指定 NDK 根目录 |
| `--output 'D:\reports\phone.json.gz'` | 指定本机新文件，父目录须存在；拒绝覆盖已有文件 |

查看完整入口帮助：`python -B .\tools\android\export_android.py --help`。

## 数据口径

- `value` 使用 `st_blocks × 512` 的分配空间，`logical` 保留普通文件的逻辑大小；文件数量按路径计数。小项目和目录自身占用合并为「小项目及目录元数据（汇总）」，字节数、逻辑大小、文件及目录数量仍计入父级；零分配的稀疏文件也保留逻辑大小和数量。
- 包含应用私有目录及 `/data/media` 下的用户文件。排除 `/data` 下的子挂载点，避免 `/data/data` 与 `/data/user/0` 等绑定目录重复统计；其他分区不在扫描范围内。不跟随符号链接，硬链接的字节数只计一次，链接路径仍计入文件数量。
- 文件系统已用量与实际枚举分配量的正差额，单独显示为「文件系统及未归档占用」；可能包含文件系统元数据、已删除但仍打开的文件及扫描期间变化。JSON 记录前后容量统计与差额。
- 扫描期间手机仍在运行，结果是实时遍历。极深目录先按阈值汇总；汇总后仍超过网页 256 层限制时会报错，需增大阈值。

## 安全与兼容性

每次从相邻的 `android_scan.c` 编译临时扫描器，复制到 `/data/local/tmp` 后以低优先级执行；结束或失败时尝试清理手机及电脑上的临时文件。不安装 APK，不修改系统配置，不读取文件内容。

根权限拒绝、遍历错误、重复目录 inode、挂载点变化、传输截断或校验失败时停止，不保存不完整结果。断开设备导致远端清理失败时，会提示临时程序路径；恢复连接后须清理该临时程序。

已在 Windows、NDK 27、一加 PJZ110、Android 16、KernelSU、F2FS 上实机验证。其他 Root 实现和电脑平台尚未实机验证。

## 测试

在仓库根目录运行 `python -B -m unittest discover -s tests -v`。测试使用合成扫描流，无需连接手机，不含私人目录或文件清单。
