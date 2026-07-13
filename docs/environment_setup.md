# Req2Web 开发环境

最后验证：2026-07-11

## 已安装环境

- Windows 原生开发环境，不依赖 WSL。
- Python 3.12.10：用户级安装。
- 项目虚拟环境：`.venv/`。
- pip 26.1.2。
- Node.js 24.12.0 LTS。
- npm 11.6.2。

VS Code 已配置为默认使用：

```text
${workspaceFolder}\.venv\Scripts\python.exe
```

## 已安装的数据处理依赖

版本记录在 `requirements-data.txt`：

- pandas：表格与 CSV 处理。
- pyarrow：读取 Vision2Web Parquet 元数据。
- Pillow：图片完整性校验。
- ijson：流式读取 RICO Semantics 大型 JSON，避免整文件载入内存。
- numpy、python-dateutil、six、tzdata：pandas 依赖。

## 常用命令

不激活虚拟环境也可以直接运行：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install -r requirements-data.txt
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

运行 Vision2Web 审计：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit_vision2web.ps1
```

只刷新 inventory、不修改 selection manifest：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit_vision2web.ps1 -NoManifestUpdate
```

PowerShell 文件只是便捷入口，实际审计逻辑位于 `scripts/audit_vision2web.py`。

运行 RICO combined 关系清点和候选生成：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit_rico_combined.ps1
```

运行 RICO filtered_traces 清点和流程候选生成：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit_rico_traces.ps1
```

## 约束

- Python 依赖统一安装到 `.venv`，不要装入系统 Python。
- Node 项目依赖后续统一写入 `package.json`，不要依赖全局 npm 包。
- `.venv`、`node_modules`、缓存和 `.env` 已由 `.gitignore` 排除。
- WSL 仅在明确需要 Linux 专属工具时再安装发行版，当前项目不需要。
