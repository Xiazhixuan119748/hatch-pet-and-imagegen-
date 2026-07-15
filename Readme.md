# Imagegen + Hatch Pet 跨电脑移植说明

这套文件用于把自定义 `imagegen` 和 `hatch-pet` 技能迁移到另一台 Windows 电脑。图片生成会通过 `imagegen` 自带的包装器读取目标电脑上的：

```text
%USERPROFILE%\.codex\imagegen.env
```

`hatch-pet` 已明确允许并要求使用这条图片生成路径。

## 1. 文件说明

需要带到另一台电脑的内容就是这四项：

```text
Desktop\
├─ .system\
│  └─ imagegen\
├─ hatch-pet\
├─ imagegen.env
└─ Readme.md
```

- `.system`：其中包含修改后的 `imagegen` 图片生成技能和 `imagegen.env` 加载包装器。
- `hatch-pet`：Codex v2 动态宠物生成、校验和打包技能。
- `imagegen.env`：图片接口地址、API Key 和模型配置。此文件包含敏感信息，不要上传到网盘公开链接、GitHub 或聊天窗口。
- `Readme.md`：本迁移说明。

## 2. 目标电脑要求

1. 安装并至少启动一次 Codex Desktop。
2. Windows 用户目录下能够创建 `%USERPROFILE%\.codex`。
3. 能够访问 `OPENAI_BASE_URL` 指向的图片接口。
4. 如运行时提示缺少 Python 包，需要安装 `openai` 和 `Pillow`。

## 3. 安装技能

不需要运行安装脚本，也不需要压缩或解压。先完全退出 Codex Desktop，再手动复制文件夹和文件。

### 3.1 安装 imagegen

把桌面上的整个 `.system` 文件夹复制到：

```text
C:\Users\你的用户名\.codex\skills\
```

复制完成后必须得到：

```text
C:\Users\你的用户名\.codex\skills\.system\imagegen\SKILL.md
```

如果目标电脑的 `skills` 目录中已经有 `.system` 文件夹，不要删除整个现有 `.system`。打开迁移包的 `.system`，只把里面的 `imagegen` 文件夹复制到目标电脑的 `.codex\skills\.system` 中。若已有同名 `imagegen`，先将旧文件夹改名备份，再放入新的 `imagegen`。

### 3.2 安装 hatch-pet

把桌面上的整个 `hatch-pet` 文件夹复制到：

```text
C:\Users\你的用户名\.codex\skills\
```

复制完成后必须得到：

```text
C:\Users\你的用户名\.codex\skills\hatch-pet\SKILL.md
```

不要复制成 `hatch-pet\hatch-pet\SKILL.md`。如果目标电脑已经有同名 `hatch-pet` 文件夹，先将旧文件夹改名备份。

### 3.3 放置配置文件

把 `imagegen.env` 复制到：

```text
C:\Users\你的用户名\.codex\imagegen.env
```

最终文件不能放在 `skills`、`imagegen` 或 `hatch-pet` 文件夹里面。

如果看不到 `.codex` 隐藏目录，可以在文件资源管理器地址栏直接输入：

```text
%USERPROFILE%\.codex
```

如果 `skills` 或 `.system` 文件夹不存在，可以手动新建。

## 4. 配置 imagegen.env

目标路径必须是：

```text
%USERPROFILE%\.codex\imagegen.env
```

文件格式：

```dotenv
OPENAI_API_KEY=替换为目标电脑使用的API_KEY
OPENAI_BASE_URL=https://你的图片接口地址/v1
OPENAI_IMAGE_MODEL=gpt-image-2
```

注意事项：

- `OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 必须存在且不能为空。
- `OPENAI_IMAGE_MODEL` 用于指定图片模型。
- 模型值必须以 `gpt-image-` 开头，例如 `gpt-image-2`。
- 包装器只读取上述三个键，不会加载文件中的其他变量。
- `imagegen.env` 中的值优先于父进程中已有的同名环境变量。
- 不要把真实 API Key 写进本 README。

## 5. 安装 Python 依赖

先尝试直接执行第 6 节的 dry-run。Dry-run 不需要联网，也不要求安装 `openai` 包。

只有真实生成时报 `No module named openai` 或类似错误时，才执行：

```powershell
py -m pip install --upgrade openai pillow
```

如果目标电脑使用 `uv` 管理 Python，也可以执行：

```powershell
uv pip install --upgrade openai pillow
```

依赖必须安装到实际调用图片脚本的 Python 环境中。如果安装后仍提示缺包，先确认报错中显示的 Python 路径，再使用该 Python 执行 `-m pip install`。

## 6. 验证配置，不生成图片

关闭并重新打开 Codex Desktop，然后在 PowerShell 中执行：

```powershell
$Imagegen = Join-Path $env:USERPROFILE '.codex\skills\.system\imagegen\scripts\image_gen_with_codex_env.py'

py $Imagegen generate `
  --prompt 'configuration test' `
  --dry-run `
  --out "$env:TEMP\imagegen-dry-run.png"
```

成功时应看到类似信息：

```text
Using Codex image configuration from C:\Users\你的用户名\.codex\imagegen.env.
OPENAI_API_KEY is set.
```

输出 JSON 中的模型应与 `OPENAI_IMAGE_MODEL` 一致。Dry-run 不会请求图片接口，也不会产生图片费用。

## 7. 在 Codex 中验证技能

重新打开 Codex 后，可以分别测试：

```text
使用 $imagegen，通过 imagegen.env 生成一张简单测试图片。
```

```text
使用 $hatch-pet 创建一个简单的 Codex v2 宠物。
```

真实图片测试会访问 `OPENAI_BASE_URL` 并可能产生接口费用。建议先用一张低成本测试图确认连接，再运行完整的 `hatch-pet` 流程。完整宠物会生成多张基础图和动画行，耗时和费用都明显高于单张图片。

## 8. 安装后的目录结构

正确安装后应为：

```text
%USERPROFILE%\.codex\
├─ imagegen.env
└─ skills\
   ├─ .system\
   │  └─ imagegen\
   │     ├─ SKILL.md
   │     ├─ scripts\
   │     │  ├─ image_gen.py
   │     │  └─ image_gen_with_codex_env.py
   │     └─ references\
   └─ hatch-pet\
      ├─ SKILL.md
      ├─ scripts\
      ├─ references\
      └─ tests\
```

不要把 `imagegen` 直接放到 `%USERPROFILE%\.codex\skills\imagegen`。当前 `hatch-pet` 使用的是系统技能路径：

```text
%USERPROFILE%\.codex\skills\.system\imagegen
```

## 9. 常见问题

### 找不到 imagegen.env

确认文件不是 `imagegen.env.txt`，并执行：

```powershell
Get-Item -Force "$env:USERPROFILE\.codex\imagegen.env"
```

### 提示缺少 OPENAI_API_KEY 或 OPENAI_BASE_URL

检查 `imagegen.env` 中对应行是否存在、拼写正确且等号右侧不为空。不要在变量名前添加空格。

### 模型名称错误

`OPENAI_IMAGE_MODEL` 必须使用 `gpt-image-*` 模型名。推荐保持：

```dotenv
OPENAI_IMAGE_MODEL=gpt-image-2
```

### 401 或鉴权失败

API Key 无效、过期，或者当前接口不接受该 Key。更换 `OPENAI_API_KEY` 后重试。

### 404 或接口路径错误

检查 `OPENAI_BASE_URL` 是否是 OpenAI SDK 兼容的基础地址。通常应包含 `/v1`，不要填写完整的 `/images/generations` 请求路径。

### 连接超时

确认目标电脑能够访问配置的域名，并检查代理、防火墙、DNS 和接口服务状态。

### Codex 没有识别新技能

确认目录结构正确，然后完全退出并重新启动 Codex Desktop。只刷新任务页面可能不足以重新加载技能。

### hatch-pet 没有使用 imagegen.env

确认安装的是本迁移包内的两个修改版技能，并检查以下文件是否存在：

```text
%USERPROFILE%\.codex\skills\.system\imagegen\scripts\image_gen_with_codex_env.py
%USERPROFILE%\.codex\skills\hatch-pet\SKILL.md
```

`hatch-pet` 不应直接调用其他图片 API 或临时脚本；所有正常视觉生成都应交给 `$imagegen` 的 `image_gen_with_codex_env.py` 包装器。

## 10. 安全建议

- 迁移完成后，从不安全的中转位置删除包含真实密钥的 `imagegen.env` 副本。
- 不要把 `imagegen.env` 提交到 Git。
- 不要在截图、终端日志或聊天中展示 API Key。
- 如果密钥曾经通过公开渠道传输，立即在接口提供方后台撤销并重新生成。
- 给配置文件仅保留当前 Windows 用户的读取权限会更稳妥。
