# 本地页面可视化验收手册

## 1. 目的与边界

本文给出 Req2Web 静态页面的人工开发验收流程，供新的工作对话复用。适用对象包括 `DeterministicPageRenderer` 的输出目录，以及 `DeterministicResultPackager` 结果包中的 `page/` 目录。

该流程只用于确认浏览器中的实际布局、样式、组件、点击反馈、状态切换和控制台错误。它不改变 `req2web.consistency.report.v1` 的结构检查语义，不向结果包加入截图，也不实现自动截图、像素级比较或最终 Demo 控制台。

## 2. 为什么不能直接打开 `file://`

生成页面本身可以离线打开，但 in-app Browser 可能按 URL 安全策略拒绝 `file:///.../index.html`。这不是工作区读写权限不足，也不能通过扩大文件系统可写目录解决。

遇到该拒绝时应立即停止重复尝试 `file://`，向用户申请一次范围明确的本地验收授权，然后改用只绑定回环地址的临时 HTTP 服务：

- 页面仍来自工作区临时目录；
- 服务只绑定 `127.0.0.1`，不得绑定 `0.0.0.0`；
- 浏览器只访问 `http://127.0.0.1:<port>`；
- 验收完成后停止服务并删除临时页面；
- 不需要长期放开 Python、PowerShell、浏览器或网络权限。

可直接向用户请求以下授权：

> 允许你重新生成两个 CLI 冒烟页面到工作区临时目录，启动一个仅绑定 `127.0.0.1:8765` 的临时静态文件服务，使用 in-app Browser 打开 `http://127.0.0.1:8765`，完成移动端和桌面端的布局、样式、组件、点击反馈、状态切换和控制台错误验收。验收完成后停止服务器并删除临时页面。如出现命令权限弹窗，我允许本次操作。

授权仅覆盖本次临时验收。命令触发权限弹窗时仍应提交范围最小的单次审批，不申请宽泛或永久规则。

## 3. 开始前检查

在仓库根目录执行只读检查：

```powershell
git status --short --branch
git branch --show-current
git rev-parse HEAD
```

确认：

- 当前仓库、分支和 HEAD 符合任务要求；
- 工作区没有可能被临时生成或清理操作覆盖的用户修改；
- 临时目录位于仓库内，例如 `.tmp_visual_acceptance/`；
- 不向 `data/raw` 写入内容；
- 记录本次服务端口，避免误停其他进程。

若工作区存在范围不明的未提交修改，应先停止并报告。

## 4. 生成两个独立页面

以下示例同时覆盖移动电商和桌面宠物识别。输出目录必须显式指定为本次专用临时目录：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_page_renderer.py `
  "做一个带搜索、购物车和结算的移动电商页面" `
  --target-device mobile `
  --output-dir .\.tmp_visual_acceptance\ecommerce-mobile

.\.venv\Scripts\python.exe .\scripts\run_page_renderer.py `
  "做一个宠物情绪识别 App，用户拍照后展示识别结果" `
  --target-device desktop `
  --constraint "相机权限被拒绝时给出恢复提示" `
  --output-dir .\.tmp_visual_acceptance\pet-desktop
```

生成后先确认两个目录均含 `index.html`、`styles.css`、`app.js` 和 `render_manifest.json`。若验收结果包，则把服务器根目录指向结果包根目录，并访问其中的 `page/index.html`。

## 5. 启动并核验临时服务

使用项目虚拟环境中的 Python，在隐藏窗口启动服务并保存本次进程对象：

```powershell
$root = (Resolve-Path .\.tmp_visual_acceptance).Path
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$server = Start-Process `
  -FilePath $python `
  -ArgumentList '-m', 'http.server', '8765', '--bind', '127.0.0.1', '--directory', $root `
  -WindowStyle Hidden `
  -PassThru
$server.Id
```

随后验证进程和页面，不要仅凭 `Start-Process` 返回成功就开始验收：

```powershell
Get-Process -Id $server.Id | Select-Object Id, ProcessName, Path
(Invoke-WebRequest -UseBasicParsing `
  http://127.0.0.1:8765/ecommerce-mobile/index.html).StatusCode
```

预期状态码为 `200`。端口冲突时应停止并报告实际占用情况；若要换端口，需要让用户授权新的回环地址 URL。不得因此改为对外网卡监听。

## 6. in-app Browser 验收步骤

明确使用 in-app Browser，而不是默认假设任意浏览器都具备相同能力。移动端建议设置 `390 x 844` 视口，桌面端建议设置 `1440 x 900` 视口；结束前恢复默认视口。

对每个页面依次完成：

1. 打开对应的 localhost URL，等待页面加载完成。
2. 获取 DOM 快照并做一次可视检查，确认标题、摘要、section 顺序和所有组件均出现。
3. 检查布局：移动端应合理堆叠，桌面端应体现横向布局或更宽内容区；两者都不应出现非预期横向滚动。
4. 使用 DOM 快照中的可见文本或稳定 `data-*` 属性创建定位器；点击前先确认定位器数量，避免误点同名控件。
5. 逐个触发主要交互，点击后定向读取目标状态、`user_feedback` 和 interaction 标识。
6. 确认 `success`、`error`、`empty` 等本样例可达状态具有可辨识样式。不可达状态只记录为未覆盖，不伪造通过。
7. 检查浏览器控制台的 error 和 warning；两者均应为空。
8. 检查页面未请求 CDN、网络字体或外部服务，静态资源均来自 localhost 页面目录。

若 PageSpec 声明明确错误恢复约束，还必须在同一 `390 x 844` 移动视口完成三条实际点击链：

1. 从 initial 执行原有主操作，确认正常进入 success。
2. 重新回到 initial，点击独立异常模拟入口，确认进入 error、原因与恢复提示可见，并核对 feedback / section 上的 interaction ID。
3. 点击 error 中唯一恢复入口，确认返回 initial、主操作重新可见且可再次进入 success。

输入错误场景不得用空字符串或随机行为隐式触发；权限拒绝场景不得接受真实浏览器权限提示。验收的是 PageSpec 驱动的确定性离线模拟。

建议为每个样例至少记录以下证据：

| 维度 | 应记录内容 |
| --- | --- |
| 页面与视口 | URL、目标设备、实际视口 |
| 结构 | section 数量、组件数量、顺序 |
| 布局 | 内容区宽度、堆叠或分栏、横向溢出结果 |
| 交互 | 点击的组件、interaction ID、目标 state ID |
| 反馈 | `user_feedback` 文本与状态样式 |
| 运行质量 | 控制台 error/warning 数量 |

横向溢出应以浏览器实际布局值核对：在包含垂直滚动条时，`document.documentElement.scrollWidth` 应等于 `clientWidth`，不要求它等于完整的 390 像素视口宽度。

浏览器截图可以在当前对话中辅助人工观察，但不要保存、提交或扩展为自动截图管线。

## 7. 运行时判断注意事项

真实点击后的 DOM 变化是首要证据。一次验收中曾出现只读脚本读取 `window.Req2WebRenderer` 为 `undefined`，但页面真实点击仍正确更新状态和反馈。因此不要把某个自定义全局变量是否可见作为唯一通过条件。

更可靠的判断顺序是：

1. 用户可见控件能够被真实点击；
2. 目标元素的 state class、`data-*` 属性或文本发生预期变化；
3. `user_feedback` 与 `InteractionSpec` 一致；
4. 控制台没有 error/warning；
5. 静态资源和网络请求符合离线边界。

如果点击没有发生状态转换、控制台报错或页面溢出，应将该项记录为验收失败。除非当前任务同时授权修复，否则不要在验收过程中顺手修改实现。

## 8. 安全清理

验收完成后先恢复浏览器视口并关闭或离开测试标签页，再按已记录的 PID 停止服务。停止前确认它确实是本次启动的 Python 进程：

```powershell
Get-Process -Id $server.Id | Select-Object Id, ProcessName, Path
Stop-Process -Id $server.Id
```

删除临时目录前必须解析并验证绝对路径仍位于当前工作区内：

```powershell
$workspace = (Resolve-Path .).Path
$target = (Resolve-Path .\.tmp_visual_acceptance).Path
if (-not $target.StartsWith($workspace + [IO.Path]::DirectorySeparatorChar)) {
    throw "Refusing to remove a path outside the workspace: $target"
}
Remove-Item -LiteralPath $target -Recurse -Force
```

最后执行：

```powershell
Get-NetTCPConnection `
  -LocalAddress 127.0.0.1 `
  -LocalPort 8765 `
  -State Listen `
  -ErrorAction SilentlyContinue
Test-Path .\.tmp_visual_acceptance
git status --short --branch
```

预期无 `8765` 监听、`Test-Path` 返回 `False`，且工作区只保留任务明确要求的修改。不要按进程名批量停止 Python，也不要删除未经路径校验的目录。

## 9. 完成报告模板

向项目负责人报告时至少说明：

- 两个样例及其目标设备、视口和 URL 路径；
- section、组件、布局和横向溢出结果；
- 实际点击过的交互、状态转换和反馈；
- success/error/empty 样式覆盖情况；
- 控制台 error/warning 结果；
- 临时服务已停止、临时页面已删除；
- 本次是人工开发验收，不代表已实现自动截图或视觉一致性检查。

只有浏览器证据和清理检查都完成后，才能报告本地可视化验收通过。若 in-app Browser 能力不可用、授权未获得或 localhost 页面无法访问，应明确报告“未完成可视化验收”，不能用静态测试结果代替。
