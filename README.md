# iTerm2 配置迁移包

保存当前 iTerm2 的完整可迁移设置与 shell 外观，在另一台 Mac 上快速恢复。配置来自 iTerm2 **3.7.6 运行中的配置与偏好设置**，包含有效默认值，避免只导出 Profile 时丢失全局外观。

[![CI](https://github.com/mercer08/iterm2-config/actions/workflows/ci.yml/badge.svg)](https://github.com/mercer08/iterm2-config/actions/workflows/ci.yml)

## 在另一台 Mac 上安装

1. 准备 Python 3、[Homebrew](https://brew.sh) 和 **iTerm2 3.7.6 或更新版本**。在“字体册 / Font Book”中下载并启用 **手札体-简 / Hannotate SC**；配置使用的 PostScript 名为 `HannotateSC-W5`。这款 macOS 字体不在公开包内。
2. 下载 [最新 Release](https://github.com/mercer08/iterm2-config/releases/latest) 中的 `iterm2-config.zip` 并解压，或克隆本仓库。
3. 保存终端任务并**完全退出 iTerm2**。从 macOS 自带的 Terminal 进入解压后的目录，运行：

   ```sh
   /bin/zsh install.command --apply --install-deps
   ```

4. 重新打开 iTerm2。安装器保留导出时的默认 Profile；另一个名为 `Paper Light Modern`。名称来自原配置，实际颜色以导出的值为准。

`--install-deps` 通过现有 Homebrew 安装 iTerm2、Powerlevel10k、zsh-autosuggestions 和 zsh-syntax-highlighting，需要联网。如果依赖已安装，可运行：

```sh
/bin/zsh install.command --apply
```

安装器不会退出应用、关闭会话或使用 `sudo`。iTerm2 仍在运行、中文字体缺失、应用版本过旧、依赖不足、目标文件是符号链接，或目标机启用了自定义偏好目录时，会停止并说明原因。通过 Finder 打开 `install.command` 时没有安装参数，只显示用法；请运行上面的显式命令。

如果已启用 **Settings → General → Preferences → Load preferences from a custom folder or URL**，先在 iTerm2 内关闭此选项，再完全退出并安装。原自定义目录不会被删除。

## 保存了什么

| 内容 | 保存方式 |
| --- | --- |
| 两个 Profile 的完整有效设置 | 全局 plist 与可手动导入的 JSON；包括所有颜色、字体、特殊字符字体映射、键盘、滚动、窗口和终端选项 |
| 142 项有效全局偏好 | 主题、标签栏、边距、鼠标、快捷键修饰键、GPU、关闭行为、Toolbelt、AI 模型及公开 API 地址等 |
| 当前字体 | Comic Shanns Mono Nerd Font **3.5.1**，普通与 Mono 两组 Regular/Bold，共四个原版字体文件 |
| shell 外观 | 当前 Powerlevel10k 双行提示符、蓝紫配色、Git 状态、时间、补全颜色、命令高亮和建议颜色 |
| Claude Code 工作组 | 通用工作组、触发器、状态优先级与代码审查提示词 |
| 可校验清单 | `manifest.json` 记录配置、字体、shell 文件的 SHA-256 |

主要外观值：`#1e1e2e` 背景、`#cdd6f4` 前景、13pt Comic Shanns Mono、13pt 手札体中文、1.2 倍纵向间距、10% 透明度、模糊半径 30、横向边距 12、纵向边距 8。保留当前顶部标签栏与单标签隐藏设置。

完整导入会替换目标机的 Profile 列表、默认 Profile 与同名公开偏好；目标机其他偏好、私有偏好域和钥匙串保留。用户目录按目标账户重新定位，动态 Profile 转成普通独立 Profile。`.zshrc` 原文保留，只追加一个可重复安装的加载块；提示符配置单独放在 `~/.config/iterm2-portable/`，原 `.p10k.zsh` 不被覆盖。

快照中的 shell 依赖版本为 Powerlevel10k 1.20.0、zsh-autosuggestions 0.7.1、zsh-syntax-highlighting 0.8.0。字体和 Powerlevel10k 基线配置随包固定；Homebrew 插件安装使用当时可用版本。若要严格复现，使用相同的应用与依赖版本。macOS 字体版本、屏幕缩放、系统外观、壁纸及透明效果会影响最终渲染，配置包不能固定这些系统条件。

## 本地备份与恢复

每次实际变更前，安装器把受影响文件及原偏好域备份到：

```text
~/Library/Application Support/iterm2-portable/backups/<unique-install-id>/
```

备份目录权限为 `700`，可能包含目标机原有私人配置，仅保存在目标机，不进入仓库或 Release。终端会打印准确的备份位置及恢复命令。需要恢复时先完全退出 iTerm2，再从 macOS Terminal 运行：

```sh
python3 scripts/install.py --restore "$HOME/Library/Application Support/iterm2-portable/backups/<unique-install-id>"
```

恢复会还原原文件、权限与原先不存在的状态，并覆盖这些文件在安装后的后续修改。Homebrew 安装或更新的依赖不会随配置恢复而卸载。

## 只导入 Profile

在 **Settings → Profiles → Other Actions → Import JSON Profiles…** 中选择 `profiles/profiles.json`，并安装 `fonts/` 内的四个 `.otf` 字体。这个 JSON 不包含全局外观和 shell 提示符；要恢复完整效果，请使用安装命令。JSON 中的 `~` 是可迁移的 home 占位符，安装器会转成新账户的绝对路径；手动导入后若启用自定义路径，请在目标机检查该路径。

也可以使用 iTerm2 的自定义偏好目录功能加载 `preferences/`，但该方式不会处理字体、shell 或 home 占位符，完整迁移仍建议使用安装器。

## 隐私与安全边界

公开包不含钥匙串/API Key、SSH 凭据、AI 授权或同意状态、聊天数据库、会话恢复数据、命令/剪贴板/搜索历史、个人路径、设备安装标识、更新分组标识、窗口历史坐标或原始导出备份。原 shell 中的个人工具、账户、代理与环境变量未整体复制。

保留 AI 模型和公开 OpenAI API 地址，也保留当前剪贴板/API 服务等功能设置；API Key、系统授权和 AI 同意仍需在新电脑单独配置。Claude Code/OMP 等应用本身、它们的登录及外部 hooks 不在本包安装范围内。

验证器会扫描公开文件的敏感内容，解析 plist、嵌入 JSON、JSON 字符串及内嵌数据，拒绝不认识的不透明二进制配置，并检查字体与配置哈希。它会在提交及发布前提供实际检查结果，但不构成对任意未来修改的安全保证。更新配置时不要直接上传原始偏好域、整个 Application Support 目录或个人 dotfiles。

## 验证和发布

以下检查不更改真实用户配置：

```sh
python3 scripts/verify.py
python3 scripts/smoke.py
python3 scripts/install.py --stage /tmp/iterm2-portable-preview
```

`--stage` 在指定目录生成完整的模拟 home，支持重复安装和恢复，不联网、不安装依赖、不访问真实偏好域。macOS smoke 还使用唯一临时偏好域执行真实 `defaults import/export` 往返，随后删除该临时域。

本地已经打开 iTerm2 时，可运行 `python3 scripts/smoke.py --require-defaults --require-running-iterm`，额外验证安装器会拒绝正在运行的应用。该检查直接调用只读保护逻辑，不执行真实导入。

配置里的图标必须是字体自带、而且正好一格宽，否则就会和后面的字符重叠。需要核对图标时用 `scripts/icon_probe.swift`（macOS 自带的 Swift 即可，上面的 Python 检查不依赖它）：

```sh
scripts/icon_probe.swift check  ComicShannsMonoNFM '\uF059' '\uF06A' '\uF055' '\u2713'
scripts/icon_probe.swift line   ComicShannsMonoNFM '\uF06A3'
scripts/icon_probe.swift config ComicShannsMonoNFM shell/p10k.zsh
```

`check` 逐个字形报告它是否存在于该字体、字面宽度占几格，并标出会回退到系统字体或比一格宽的字形；`line` 交给 CoreText 真正排版一段文本，列出每一段实际使用的字体，用来发现悄悄回退到 Menlo 等字体的情况；`config` 扫描 shell 配置里会用到的所有字形，Powerlevel10k 的 `icons.zsh` 只读取本包所用的 `nerdfont-complete` preset 分支。三种模式默认只报告，加 `--strict` 后一旦发现问题就以非零状态退出。

GitHub Actions 在 Linux 上执行隐私和完整性检查，在 macOS 上执行迁移 smoke；`v*` 标签通过检查后，由 CI 打包并发布 ZIP。发布包只取明确允许的已跟踪文件，再验证 ZIP 内容。工作流默认只有读取权限，发布 job 单独获得 `contents: write`。

工作流使用固定提交的 `actions/checkout v7.0.1`，关闭 checkout 凭据持久化；公开仓库不需要配置额外 CI secrets。

## 许可与来源

本仓库脚本及原创配置使用 MIT License。字体与 Powerlevel10k 保留各自许可证，见 `fonts/` 与 `shell/POWERLEVEL10K-LICENSE.txt`。Comic Shanns 字体资产直接来自 Nerd Fonts v3.5.1，未修改；图标来源见 `fonts/ATTRIBUTION.md`。手札体为 Apple 字体，需要在目标 Mac 上自行启用。

相关文档：[iTerm2 Python Profile API](https://iterm2.com/python-api/profile.html)、[偏好设置](https://iterm2.com/documentation-preferences-general.html)、[Nerd Fonts](https://github.com/ryanoasis/nerd-fonts)、[Powerlevel10k](https://github.com/romkatv/powerlevel10k)。
