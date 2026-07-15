# Codex Third-Party ImageGen & Hatch Pet

让 Codex 在不修改原有配置的情况下，通过第三方 OpenAI 兼容中转站生成图片，并创建可在 Codex Desktop 中使用的动态 Pet。

本项目包含两个 Codex Skill：

- **ImageGen**：从独立的 `imagegen.env` 读取图片接口地址、API Key 和模型，不覆盖 Codex 原有配置。
- **Hatch Pet**：根据文字描述、参考图或品牌特征，生成、校验并打包 Codex v2 动态 Pet。

> [!IMPORTANT]
> 仓库中的 `imagegen.env` 仅为脱敏模板。请勿将真实 API Key 提交到 GitHub。

## 功能特点

### 使用第三方中转站生图

ImageGen 通过独立包装器读取：

```text
%USERPROFILE%\.codex\imagegen.env
```

它只读取以下三个变量：

```dotenv
OPENAI_API_KEY=你的API_KEY
OPENAI_BASE_URL=https://你的中转站地址/v1
OPENAI_IMAGE_MODEL=gpt-image-2
```

这套方式不需要修改 Codex 原有配置文件，也不会改变 Codex 的模型、登录方式或其他环境设置。配置只传递给当前图片生成子进程。

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
- 可访问的 OpenAI Images API 兼容中转站
- 中转站支持 `gpt-image-*` 图片模型

真实生成图片时可能需要安装：

```powershell
py -m pip install --upgrade openai pillow
```

Dry-run 配置测试不联网，也不要求安装 `openai`。

## 安装

### 1. 克隆仓库

```powershell
git clone https://github.com/Xiazhixuan119748/hatch-pet-and-imagegen-.git
Set-Location hatch-pet-and-imagegen-
```

安装前请完全退出 Codex Desktop。

### 2. 安装 ImageGen

在 PowerShell 中执行：

```powershell
$CodexHome = Join-Path $env:USERPROFILE '.codex'
$SkillHome = Join-Path $CodexHome 'skills'

New-Item -ItemType Directory -Force -Path "$SkillHome\.system" | Out-Null
Copy-Item '.\.system\imagegen' "$SkillHome\.system" -Recurse -Force
```

安装后应存在：

```text
%USERPROFILE%\.codex\skills\.system\imagegen\SKILL.md
```

如果已经安装过同名 ImageGen Skill，建议先备份原目录。不要删除 `.system` 中的其他系统 Skill。

### 3. 安装 Hatch Pet

```powershell
Copy-Item '.\hatch-pet' $SkillHome -Recurse -Force
```

安装后应存在：

```text
%USERPROFILE%\.codex\skills\hatch-pet\SKILL.md
```

注意不要复制成 `hatch-pet\hatch-pet\SKILL.md`。

### 4. 配置图片中转站

先把仓库中的脱敏模板复制到 Codex 目录：

```powershell
Copy-Item '.\imagegen.env' "$CodexHome\imagegen.env"
notepad "$CodexHome\imagegen.env"
```

只编辑 `%USERPROFILE%\.codex\imagegen.env` 这个副本，填写自己的配置：

```dotenv
OPENAI_API_KEY=替换为你的API_KEY
OPENAI_BASE_URL=https://你的中转站地址/v1
OPENAI_IMAGE_MODEL=gpt-image-2
```

配置要求：

- `OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 不能为空。
- `OPENAI_BASE_URL` 应填写 OpenAI SDK 兼容基础地址，通常以 `/v1` 结尾。
- 不要填写完整的 `/images/generations` 接口路径。
- `OPENAI_IMAGE_MODEL` 必须是 `gpt-image-*` 模型，例如 `gpt-image-2`。
- 请确认中转站实际支持所填写的图片模型和 Images API。

完成后重新启动 Codex Desktop，使 Skill 被重新加载。

## 验证配置

在 PowerShell 中运行 dry-run：

```powershell
$Imagegen = Join-Path $env:USERPROFILE '.codex\skills\.system\imagegen\scripts\image_gen_with_codex_env.py'

py $Imagegen generate `
  --prompt 'configuration test' `
  --dry-run `
  --out "$env:TEMP\imagegen-dry-run.png"
```

成功时会显示配置文件路径，并提示 API Key 已设置。输出中的模型应与 `OPENAI_IMAGE_MODEL` 一致。Dry-run 不会请求中转站、不会生成图片，也不会产生图片费用。

## 使用方法

### 通过第三方中转站生成图片

在 Codex 中输入类似提示：

```text
使用 $imagegen 和 imagegen.env 中配置的接口，生成一张赛博朋克城市夜景。
```

ImageGen 会读取独立配置并调用中转站。真实生成会产生相应接口费用。

### 创建 Codex Pet

在 Codex 中输入：

```text
使用 $hatch-pet 创建一个蓝白配色、3D 玩具风格的机器人 Pet。
```

也可以附加参考图：

```text
使用 $hatch-pet，根据这张参考图创建一个 Codex v2 动态 Pet，保持角色的颜色和主要特征。
```

Hatch Pet 会自动调用本项目的 ImageGen 配置生成视觉素材。完整 Pet 需要生成多组动画图片，所需时间和接口费用明显高于单张图片生成。

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

### 提示缺少 OPENAI_API_KEY 或 OPENAI_BASE_URL

检查配置项拼写、等号右侧内容以及文件路径。不要在变量名前添加空格。

### 返回 401 或鉴权失败

API Key 可能无效、过期，或者中转站不接受该 Key。请在中转站后台确认密钥权限。

### 返回 404 或接口路径错误

确认 `OPENAI_BASE_URL` 是 OpenAI SDK 兼容基础地址。通常应包含 `/v1`，但不应包含完整的 `/images/generations` 路径。

### 提示模型名称错误

`OPENAI_IMAGE_MODEL` 必须使用 `gpt-image-*` 名称，并且中转站必须支持该模型。

### 提示缺少 Python 模块

执行：

```powershell
py -m pip install --upgrade openai pillow
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
