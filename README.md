# 空间树图 · 通用查看器

受 SpaceSniffer 交互方式启发的离线矩形树图查看器。单个 HTML 文件，无需安装依赖、构建或启动服务。

## 使用

1. 下载仓库中的 `index.html`，用新版 Chrome、Edge、Firefox 或 Safari 打开。
2. 点击「打开数据…」，或把数据文件拖入窗口。
3. 可同时打开多个同单位的数据文件，通过顶部下拉框切换数据集。

支持 WizTree CSV、树形 JSON、Gzip 压缩的 JSON，以及使用 `host` / `drives` / `bytes` 结构的既有磁盘快照。

所有解析均在浏览器本地完成，无外部请求，不上传数据。刷新页面后重新打开数据即可。

## 操作

- **单击分组／目录**：在原矩形内展开，保留周围布局；换选其他分支时切换临时展开。
- **单击叶子／文件**：选中并显示状态信息，悬停查看详情。
- **双击分组／目录**：放大到该分支。
- **Less detail / More detail**：逐层收起／展开全图，同时保留选中分支的展开状态。也支持 `-` / `+` 快捷键。
- **后退／前进**：切换浏览历史；**上一级**或 `Backspace`：返回实际父级。
- **搜索**：按名称或路径查找；`Esc` 关闭搜索结果。

## JSON 数据格式

节点使用 `name`、`value`、`children`。父节点未提供 `value` 时，自动累加子项。数值须为非负有限数。

```json
{
  "name": "项目工时",
  "unit": "小时",
  "children": [
    { "name": "设计", "value": 24 },
    {
      "name": "开发",
      "children": [
        { "name": "前端", "value": 48 },
        { "name": "后端", "value": 80 }
      ]
    },
    {
      "name": "测试",
      "children": [
        { "name": "自动化", "value": 32 },
        { "name": "验收", "value": 16 }
      ]
    }
  ]
}
```

- `unit: "bytes"`：自动显示 B / KiB / MiB / GiB；其他单位作为数值后缀，也可省略。
- 多数据集格式：`{ "name": "汇总", "unit": "小时", "datasets": [树一, 树二] }`。
- 可选节点字段：`path`、`kind`、`modified`；磁盘数据还可包含 `logical`、`files`、`folders`、`mft`、`capacity`、`free`。
- 页面脚本接口：`window.spaceTree.load(data)`，接收相同格式的对象。

## WizTree CSV

在目标设备上用 WizTree 扫描并导出 CSV，再导入查看器。浏览器不直接扫描磁盘或读取 MFT。

CSV 支持英文和简体中文表头，需要 `File Name` / `文件名称` 和 `Allocated` / `分配` 或 `Size` / `大小` 列，使用 Windows 绝对路径；优先按“分配”展示。“修改时间”“文件”“文件夹”等中文元数据列也会读取。支持逗号、分号、制表符分隔，以及 UTF-8、带 BOM 的 UTF-16 和 GBK 中文导出。

导入时按路径重建目录，保留小文件，不设置 16 MiB 合并阈值。源数据已合并或未列出的明细会显示为合并项目；子项统计重叠时按权重归一化展示。

回归测试：在仓库根目录运行 `python -m http.server 8000 --bind 127.0.0.1`，打开 `http://127.0.0.1:8000/tests/import.html`，结果应全部为 `passed: true`。测试通过实际文件导入入口覆盖中英文表头、三种分隔符、“分配”优先和“大小”回退，不包含真实磁盘数据。

## 数据导出工具

两个工具按数据来源独立存放，均输出可直接拖入查看器的 JSON.GZ，默认保存到电脑「下载」文件夹，拒绝覆盖已有文件。完整依赖、参数、数据口径和兼容性说明见各自文档。

| 工具 | 采集方式 | 计量口径 | 默认明细 |
| --- | --- | --- | --- |
| [Storage Analyzer](tools/storage_analyzer/README.md) | 通过 SSH 读取 NAS 上已完成的报告 | 逻辑大小 | 各级目录，直属文件汇总 |
| [Android Root](tools/android/README.md) | 通过 ADB 在手机上遍历 `/data` 元数据 | 分配空间 | 至少 50 MB 的文件和目录，小项目汇总 |

在仓库根目录运行。NAS 入口需要 **PowerShell 7**，将 `user@nas` 换成自己的 SSH 目标：

```powershell
.\tools\storage_analyzer\Export-StorageAnalyzer.ps1 -Nas user@nas
```

Android 入口需要电脑上的 **Python 3.8+、ADB、NDK** 和已授予 Root 的 ARM64 Android 手机：

```powershell
python -B .\tools\android\export_android.py
```

```text
index.html                         通用查看器
tools/
  storage_analyzer/                NAS 导出入口、Python 转换器及使用说明
  android/                         Android 导出入口、C 扫描器及使用说明
tests/                             查看器导入测试及两个导出工具的合成数据测试
```

导出工具回归测试：`python -B -m unittest discover -s tests -v`。无需连接 NAS 或手机，不包含真实用户数据。查看器导入测试见上方「WizTree CSV」。

## 范围与限制

- 只读展示，不打开、修改或删除源文件。
- 导入失败会显示错误，并保留当前视图；不同单位的数据文件须分开打开。
- 文件一次性读入内存，大型 CSV 的容量受浏览器内存限制，尚未验证百万行 CSV。
- 已用真实 WizTree CSV、六盘 JSON.GZ 快照及普通树形 JSON 验证导入、导航、细节切换、搜索、拖拽和窄屏布局。
- 仓库仅包含查看器、导出工具源码、测试和说明，不包含任何实际磁盘快照或用户文件清单。导出文件请保存在仓库外，勿提交私人报告。
