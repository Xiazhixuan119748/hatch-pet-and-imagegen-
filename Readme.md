# Codex Third-Party ImageGen & Hatch Pet

让 Codex 在不修改原有配置的情况下，通过第三方 OpenAI 兼容中转站、Google Gemini、xAI Grok Imagine 或 Agnes AI 生成图片，并创建可在 Codex Desktop 中使用的动态 Pet。

本项目包含两个 Codex Skill：

- **ImageGen**：从独立的 `imagegen.env` 读取图片接口地址、API Key 和模型，不覆盖 Codex 原有配置。
- **Hatch Pet**：根据文字描述、参考图或品牌特征，生成、校验并打包 Codex v2 动态 Pet。

> [!IMPORTANT]
> 仓库中的 `imagegen.env` 仅为脱敏模板。请勿将真实 API Key 提交到 GitHub。

## 相对官方 Skill 的改造内容

本项目不是官方 Skill 的原样镜像，而是在官方 ImageGen 与 Hatch Pet 基础上的第三方 Provider 适配版。主要改动如下：

- ImageGen 新增 `IMAGE_PROVIDER` 路由，支持 OpenAI/OpenAI 兼容接口、Google Gemini、xAI Grok Imagine 和 Agnes AI。
- 只要 `%USERPROFILE%\.codex\imagegen.env` 存在，普通生图和编辑请求会默认使用其中选择的 Provider；无需在每次提示中指定配置文件。
- Gemini 支持 Generate Content 与 Interactions 两种 API 格式、多参考图编辑以及批量任务。
- Grok 使用 xAI JSON 接口完成生成和编辑，并支持 Files API；Hatch Pet 会在参考图超过 3 张时确定性整理输入。
- Agnes 使用其 Provider 专用 JSON 请求格式，支持 Image 2.0/2.1、文生图、多图编辑及批量任务。
- Hatch Pet 可通过上述任一 Provider 生成视觉素材，并保留完整的 v2 Atlas 组装、方向语义、透明背景和最终 QA 流程。
- 配置 wrapper 只向子进程传递当前 Provider 的环境变量，不会把其他 Provider 的凭证混入请求环境，也不会在配置错误时静默切换 Provider。

> [!NOTE]
> Provider 的尺寸、宽高比、参考图数量和透明背景能力不同。API 请求成功不代表 Pet 行图一定合格，Hatch Pet 仍会执行统一的帧数、身份一致性、方向和 Atlas 校验。

安装本项目会覆盖当前安装的同名 Skill。建议按照下方步骤先备份官方原始 ImageGen 与 Hatch Pet；不再使用改造版时，可恢复备份或重新安装官方 Skill。

## 功能特点

### 使用 OpenAI、Gemini、Grok 或 Agnes 生图

ImageGen 通过独立包装器读取：

```text
%USERPROFILE%\.codex\imagegen.env
```

通过 `IMAGE_PROVIDER` 选择 Provider。OpenAI 或兼容中转站配置：

```dotenv
IMAGE_PROVIDER=openai
OPENAI_API_KEY=你的API_KEY
OPENAI_BASE_URL=https://你的中转站地址/v1
OPENAI_IMAGE_MODEL=gpt-image-2
```

Google Gemini 配置：

```dotenv
IMAGE_PROVIDER=gemini
GEMINI_API_KEY=你的Gemini_API_KEY
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
GEMINI_API_MODE=generate-content
# GEMINI_BASE_URL=https://可选的Gemini中转站地址
```

xAI Grok Imagine 配置：

```dotenv
IMAGE_PROVIDER=grok
XAI_API_KEY=你的xAI_API_KEY
XAI_IMAGE_MODEL=grok-imagine-image-quality
# XAI_BASE_URL=https://api.x.ai/v1
```

Agnes AI 配置：

```dotenv
IMAGE_PROVIDER=agnes
AGNES_API_KEY=你的Agnes_API_KEY
AGNES_IMAGE_MODEL=agnes-image-2.1-flash
# AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
```

未设置 `IMAGE_PROVIDER` 时默认使用 `openai`，兼容原配置。包装器只把当前 Provider 所需配置传给图片生成子进程，不会把另一 Provider 的凭据混入环境。

支持的主要图片任务包括：

- 文生图
- 图片编辑与参考图生成
- 多张图片或多版本生成
- 透明背景素材处理
- 网站、游戏、产品图和 Sprite 等项目资源生成

### 创建 Codex Pet

Hatch Pet 可以根据以下内容创建 Codex v2 动态 Pet：

- 文字描述
- 人物或角色参考图
- 品牌、产品或公司特征
- 已有 Pet 或 Sprite Atlas

Hatch Pet 与 ImageGen 共用 `%USERPROFILE%\.codex\imagegen.env`，并自动使用 `IMAGE_PROVIDER` 当前选择的 OpenAI、Gemini、Grok 或 Agnes。无需为 Hatch Pet 单独配置 OpenAI 凭证；只需提供所选 Provider 对应的凭证。

生成流程包括基础形象、标准动画、16 个观察方向、透明背景处理、Atlas 校验和最终打包。最终 Pet 使用 `spriteVersionNumber: 2`，Atlas 规格为 `8 x 11`，包含 9 行标准动画和 2 行观察方向动画。

## 项目结构

```text
.
|-- .system/
|   `-- imagegen/              # 修改后的 ImageGen Skill
|       |-- SKILL.md
|       |-- scripts/
|       `-- references/
|-- hatch-pet/                 # Codex Pet 创建与校验 Skill
|   |-- SKILL.md
|   |-- scripts/
|   |-- references/
|   `-- tests/
|-- imagegen.env               # 已脱敏的配置模板
|-- .gitignore
`-- Readme.md
```

## 环境要求

- Codex Desktop
- Windows 11，或其他受 Codex Desktop 支持的系统
- Python 3.10 或更高版本
- 可访问的 OpenAI Images API 兼容中转站、Google Gemini API、xAI Imagine API 或 Agnes AI API
- 对应 Provider 支持所配置的图片模型

真实生成图片时可能需要安装：

```powershell
py -m pip install --upgrade openai google-genai httpx pillow
```

Dry-run 配置测试不联网，也不要求安装对应 Provider SDK 或 HTTP 客户端。

## 安装

### 1. 克隆仓库

```powershell
git clone https://github.com/Xiazhixuan119748/hatch-pet-and-imagegen-.git
Set-Location hatch-pet-and-imagegen-
```

安装前请完全退出 Codex Desktop。

### 2. 备份官方原始 Skill

本项目会替换 Codex 当前安装的 ImageGen 和 Hatch Pet。建议先保存官方原始版本，以便不再使用本项目的改造版时随时恢复。

在 PowerShell 中执行：

```powershell
$CodexHome = Join-Path $env:USERPROFILE '.codex'
$SkillHome = Join-Path $CodexHome 'skills'
$BackupRoot = Join-Path $CodexHome ("skill-backups\before-third-party-imagegen-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

$OfficialImageGen = Join-Path $SkillHome '.system\imagegen'
$OfficialHatchPet = Join-Path $SkillHome 'hatch-pet'

if (Test-Path $OfficialImageGen) {
    Copy-Item $OfficialImageGen (Join-Path $BackupRoot 'imagegen') -Recurse -Force
}
if (Test-Path $OfficialHatchPet) {
    Copy-Item $OfficialHatchPet (Join-Path $BackupRoot 'hatch-pet') -Recurse -Force
}

Write-Host "Official Skill backup: $BackupRoot"
```

记下命令输出的备份目录。备份只需执行一次；不要把备份目录提交到 GitHub。

### 3. 安装 ImageGen

在 PowerShell 中执行：

```powershell
New-Item -ItemType Directory -Force -Path "$SkillHome\.system" | Out-Null
Copy-Item '.\.system\imagegen' "$SkillHome\.system" -Recurse -Force
```

安装后应存在：

```text
%USERPROFILE%\.codex\skills\.system\imagegen\SKILL.md
```

不要删除 `.system` 中的其他系统 Skill。

### 4. 安装 Hatch Pet

```powershell
Copy-Item '.\hatch-pet' $SkillHome -Recurse -Force
```

安装后应存在：

```text
%USERPROFILE%\.codex\skills\hatch-pet\SKILL.md
```

注意不要复制成 `hatch-pet\hatch-pet\SKILL.md`。

### 5. 配置图片 Provider

先把仓库中的脱敏模板复制到 Codex 目录：

```powershell
Copy-Item '.\imagegen.env' "$CodexHome\imagegen.env"
notepad "$CodexHome\imagegen.env"
```

只编辑 `%USERPROFILE%\.codex\imagegen.env` 这个副本，填写自己的配置：

```dotenv
IMAGE_PROVIDER=openai
OPENAI_API_KEY=替换为你的API_KEY
OPENAI_BASE_URL=https://你的中转站地址/v1
OPENAI_IMAGE_MODEL=gpt-image-2
```

切换到 Gemini 时改为：

```dotenv
IMAGE_PROVIDER=gemini
GEMINI_API_KEY=替换为你的Gemini_API_KEY
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
GEMINI_API_MODE=generate-content
```

切换到 Grok 时改为：

```dotenv
IMAGE_PROVIDER=grok
XAI_API_KEY=替换为你的xAI_API_KEY
XAI_IMAGE_MODEL=grok-imagine-image-quality
```

切换到 Agnes 时改为：

```dotenv
IMAGE_PROVIDER=agnes
AGNES_API_KEY=替换为你的Agnes_API_KEY
AGNES_IMAGE_MODEL=agnes-image-2.1-flash
```

配置要求：

- 只校验 `IMAGE_PROVIDER` 当前选择的 Provider；不会要求同时配置四套凭证，也不会自动回退到其他 Provider。
- `IMAGE_PROVIDER=openai` 时，`OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 不能为空。
- `OPENAI_BASE_URL` 应填写 OpenAI SDK 兼容基础地址，通常以 `/v1` 结尾。
- 不要填写完整的 `/images/generations` 接口路径。
- `OPENAI_IMAGE_MODEL` 必须是 `gpt-image-*` 模型，例如 `gpt-image-2`。
- 请确认中转站实际支持所填写的图片模型和 Images API。
- `IMAGE_PROVIDER=gemini` 时只要求 `GEMINI_API_KEY`；未配置 `GEMINI_BASE_URL` 时使用 Google 官方地址。
- `GEMINI_API_MODE` 默认为兼容中转站的旧版 `generate-content`；需要新版 Interactions API 时设置为 `interactions`。
- Gemini 推荐 `gemini-3.1-flash-image`。Lite 模型仅适合 1K 草稿，不建议用于完整 Pet。
- `IMAGE_PROVIDER=grok` 时只要求 `XAI_API_KEY`；未配置 `XAI_BASE_URL` 时使用 `https://api.x.ai/v1`。
- Grok 推荐 `grok-imagine-image-quality`。生成兼容 OpenAI SDK，但编辑必须使用 xAI JSON 接口，最多 3 张参考图。
- `IMAGE_PROVIDER=agnes` 时只要求 `AGNES_API_KEY`；未配置 Base URL 时使用 `https://apihub.agnes-ai.com/v1`。
- Agnes 推荐 `agnes-image-2.1-flash`。编辑仍调用 `/images/generations`，图片和 `response_format` 必须放在 `extra_body`，不能直接复用标准 OpenAI edit。

用于 Hatch Pet 时还需注意：

- Gemini 最宽支持 `21:9`，推荐 `gemini-3.1-flash-image`；Hatch Pet 会保留精确帧数要求，并通过后续提取与 QA 拒绝不合格行图。
- Grok 最宽支持 `20:9`，单次编辑最多接收 3 张参考图；Hatch Pet 会确定性合并超出的参考资料，同时保留布局参考。
- Agnes 最宽使用 `21:9`，本地最多接收 16 张有序参考图。
- 非 OpenAI Provider 的 API 请求成功不等于 Pet 行图合格；所有 Provider 仍须通过相同的帧数、角色一致性、方向语义、透明背景和 Atlas 校验。

完成后重新启动 Codex Desktop，使 Skill 被重新加载。

### 恢复官方原始 Skill

不再使用本项目的改造版时，先完全退出 Codex Desktop，再运行下面的 PowerShell。把第一行替换为安装前记录的实际备份目录：

```powershell
$BackupRoot = 'C:\Users\你的用户名\.codex\skill-backups\before-third-party-imagegen-YYYYMMDD-HHMMSS'
$CodexHome = Join-Path $env:USERPROFILE '.codex'
$SkillHome = Join-Path $CodexHome 'skills'
$DisabledRoot = Join-Path $CodexHome ("skill-backups\disabled-third-party-imagegen-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))

New-Item -ItemType Directory -Force -Path $DisabledRoot | Out-Null

$InstalledImageGen = Join-Path $SkillHome '.system\imagegen'
$InstalledHatchPet = Join-Path $SkillHome 'hatch-pet'

if (Test-Path $InstalledImageGen) {
    Move-Item $InstalledImageGen (Join-Path $DisabledRoot 'imagegen')
}
if (Test-Path $InstalledHatchPet) {
    Move-Item $InstalledHatchPet (Join-Path $DisabledRoot 'hatch-pet')
}
if (Test-Path (Join-Path $BackupRoot 'imagegen')) {
    Copy-Item (Join-Path $BackupRoot 'imagegen') (Join-Path $SkillHome '.system') -Recurse -Force
}
if (Test-Path (Join-Path $BackupRoot 'hatch-pet')) {
    Copy-Item (Join-Path $BackupRoot 'hatch-pet') $SkillHome -Recurse -Force
}

Write-Host "Modified Skill backup: $DisabledRoot"
```

此恢复流程会先保存当前改造版，再复制回原始版本，不会直接删除任一版本。恢复后重新启动 Codex Desktop。如果安装前某个官方 Skill 不存在，对应备份也不会存在，恢复时会跳过它。

## 验证配置

在 PowerShell 中运行 dry-run：

```powershell
$Imagegen = Join-Path $env:USERPROFILE '.codex\skills\.system\imagegen\scripts\image_gen_with_codex_env.py'

py $Imagegen generate `
  --prompt 'configuration test' `
  --dry-run `
  --out "$env:TEMP\imagegen-dry-run.png"
```

成功时会显示配置文件路径和所选 Provider。输出中的模型应与当前 Provider 的 `*_IMAGE_MODEL` 一致。Dry-run 不会请求图片 API、不会生成图片，也不会产生图片费用。

## 使用方法

### 通过已配置 Provider 生成图片

在 Codex 中输入类似提示：

```text
使用 $imagegen 生成一张赛博朋克城市夜景。
```

只要 `%USERPROFILE%\.codex\imagegen.env` 存在，ImageGen 就会默认读取该配置并调用 `IMAGE_PROVIDER` 所选的 Provider，不需要在每次提示中重复指定配置文件。配置文件不存在时才使用 Codex 内置图片生成。真实生成会产生相应接口费用。

### 创建 Codex Pet

在 Codex 中输入：

```text
使用 $hatch-pet 创建一个蓝白配色、3D 玩具风格的机器人 Pet。
```

也可以附加参考图：

```text
使用 $hatch-pet，根据这张参考图创建一个 Codex v2 动态 Pet，保持角色的颜色和主要特征。
```

Hatch Pet 会自动读取 `imagegen.env` 中的 `IMAGE_PROVIDER`，调用对应的 OpenAI、Gemini、Grok 或 Agnes 生成实现。完整 Pet 需要生成多组动画图片，所需时间和接口费用明显高于单张图片生成。

## Windows 10 用户提醒

> [!WARNING]
> Codex Desktop 在 Windows 10 上可能显示需要修复 workspace dependency，但安装会失败，因为 Windows 10 当前不受支持。

相关问题与解决进展请查看 OpenAI Codex 官方 Issue：

**[Codex Desktop shows workspace dependency repair on Windows 10, but install fails because Windows 10 is unsupported](https://github.com/openai/codex/issues/19811)**

如果你在 Windows 10 上遇到 workspace dependency repair、依赖安装失败或运行时不可用等问题，请前往该 Issue 查看讨论和对应解决方法。该问题来自 Codex Desktop 的系统支持限制，并非本项目的 ImageGen 或 Hatch Pet 配置错误。

## 常见问题

### Codex 没有识别 Skill

完全退出并重新启动 Codex Desktop，然后检查以下路径：

```text
%USERPROFILE%\.codex\skills\.system\imagegen\SKILL.md
%USERPROFILE%\.codex\skills\hatch-pet\SKILL.md
```

### 找不到 imagegen.env

确认配置文件位于：

```text
%USERPROFILE%\.codex\imagegen.env
```

不要把它放进 `skills`、`imagegen` 或 `hatch-pet` 文件夹。还需确认文件名不是 `imagegen.env.txt`。

### 提示缺少 Provider 配置

OpenAI 模式检查 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`；Gemini 模式检查 `GEMINI_API_KEY`；Grok 模式检查 `XAI_API_KEY`；Agnes 模式检查 `AGNES_API_KEY`。同时检查 `IMAGE_PROVIDER` 拼写、等号右侧内容以及文件路径。

### 返回 401 或鉴权失败

API Key 可能无效、过期，或者中转站不接受该 Key。请在中转站后台确认密钥权限。

### 返回 404 或接口路径错误

确认 `OPENAI_BASE_URL` 是 OpenAI SDK 兼容基础地址。通常应包含 `/v1`，但不应包含完整的 `/images/generations` 路径。

### 提示模型名称错误

OpenAI 模式使用 `gpt-image-*`；Gemini 模式使用受支持的 `gemini-*-image`；Grok 模式使用 `grok-imagine-image-quality`；Agnes 模式使用 `agnes-image-2.0-flash` 或 `agnes-image-2.1-flash`。对应服务必须支持所配置模型。

### 提示缺少 Python 模块

执行：

```powershell
py -m pip install --upgrade openai google-genai httpx pillow
```

依赖必须安装到实际运行图片脚本的 Python 环境。如果安装后仍提示缺包，请根据报错中的 Python 路径使用对应解释器执行 `-m pip install`。

## 安全说明

- 仓库中的 `imagegen.env` 是脱敏模板，不包含可用凭据。
- 请在复制后的 `%USERPROFILE%\.codex\imagegen.env` 中填写真实密钥。
- 不要把真实 API Key 写回仓库中的模板文件。
- 不要在 GitHub、截图、终端日志或聊天记录中展示真实 API Key。
- 如果密钥曾被公开，请立即在中转站后台撤销并重新生成。
- 包装器不会打印 API Key，也不会把凭据作为命令行参数传递。

## License

ImageGen 和 Hatch Pet 目录中的内容分别遵循各自 `LICENSE.txt` 中的许可条款。
