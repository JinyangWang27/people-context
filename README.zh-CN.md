# people-context

[English](README.md) | 简体中文

**你的 Agent 已经记得代码库，现在也能记得你身边的人。**

`people-context` 是一个本地优先的 [MCP](https://modelcontextprotocol.io) 服务器和 CLI，为 AI Agent
提供关于你身边之人的持久记忆：对方是谁、你们如何认识、最近约定了什么，以及对方偏好的沟通方式。
所有数据都保存在你电脑上的一个 SQLite 文件里——无需账号，不依赖云端，服务器本身不会发起网络请求。

![pctx 演示：生成虚构数据、列出联系人并输出简报](docs/assets/demo.gif)

## 为什么需要它

当你问助手“我该怎么和林经理讨论延期？”时，它通常缺少真正重要的背景：你说的是哪位林经理、
你们上周约定了什么、她更喜欢简短邮件还是临时电话，以及哪些事情仍待跟进。

`people-context` 把这些零散信息整理成 Agent 可以按需查询的结构化上下文，帮助你：

- **准备工作沟通：** 会前回顾对方的职责、近期互动、未完成事项和沟通偏好。
- **维护朋友关系：** 记住近况、重要日期和许久未联系的人，而不是依赖模糊印象。
- **处理家庭沟通：** 在尊重自己边界的同时，根据真实记录组织更合适的表达。
- **避免认错人：** 根据姓名、昵称、别名和账号返回带理由的候选人；有歧义时不会擅自猜测。
- **安全导入资料：** 邮件、通讯录、日历、LinkedIn、Outlook 和 WhatsApp 导出内容先进入审核区，
  只有确认后才会写入，原始内容不会被保存。

它提供的是**人际沟通与关系记忆辅助**，不是对他人动机的揣测，也不会替你自动发送消息或操控关系。

## 先用虚构数据试试看

最快的体验方式不会读取或修改你的真实数据库：

```bash
uvx --from people-context pctx demo --reset
```

该命令会创建一个独立的虚构数据库，并输出启动 MCP 服务器和调用工具的具体命令。生成的数据只包含
虚构人物、组织、互动和关系，因此你可以先体验身份解析、关系图谱与会前简报，再决定是否记录真实信息。

然后可以在 Agent 中尝试：

> Amina 是谁？
>
> 记住：Open City Lab 的 Amina 喜欢简短邮件，不喜欢没有预告的电话。
>
> 明天和 Daniel 开会前，我应该了解什么？

## 快速开始

需要 Python 3.11+ 和 [`uv`](https://docs.astral.sh/uv/)。

### Claude Code

```bash
claude plugin marketplace add JinyangWang27/people-context
claude plugin install people-context@people-context-plugins
```

重启 Claude Code 或运行 `/reload-plugins`。安装后可使用 MCP 服务器，以及
`/people-context:who`、`/people-context:remember` 和 `/people-context:reminders`。

### Claude Desktop

从 [最新版本](https://github.com/JinyangWang27/people-context/releases/latest)下载
`people-context.mcpb` 并打开。Claude Desktop 会使用自带的 `uv` 运行环境安装固定版本。
详细说明见 [桌面端与编辑器文档（英文）](docs/desktop-and-editors.md)。

### Codex

```bash
codex plugin marketplace add JinyangWang27/people-context
codex plugin add people-context@people-context-plugins
```

安装后请启动新的 Codex 会话。详细说明见 [Codex 插件文档（英文）](docs/codex-plugin.md)。

### Cursor、Windsurf、VS Code 或其他 MCP Client

在客户端的 MCP 配置中加入 stdio 服务器：

```json
{
  "mcpServers": {
    "people-context": {
      "command": "uvx",
      "args": ["--from", "people-context", "people-context"]
    }
  }
}
```

VS Code 使用 `servers` 键并需要设置 `"type": "stdio"`；下面的 CLI 命令会自动写入对应格式。

也可以让 CLI 写入配置：

```bash
uvx --from people-context pctx setup cursor
```

`cursor` 也可以替换为 `windsurf`、`vscode` 或 `claude-desktop`；加上 `--dry-run` 可先预览修改。
各编辑器的完整说明见 [桌面端与编辑器文档（英文）](docs/desktop-and-editors.md)。

### OpenClaw

```bash
openclaw plugins install clawhub:openclaw-plugin-people-context
```

该原生插件连接到需要主动启用的本地回环 HTTP 服务器。详细说明见
[OpenClaw 插件文档（英文）](docs/openclaw-plugin.md)。

### 仅使用 CLI

```bash
uv tool install people-context
pctx init
pctx --help
```

例如：

```bash
pctx remember "Amina Hassan" "prefers short emails" --org "Open City Lab"
pctx brief "Amina Hassan"
```

### Docker

```bash
docker run --rm -i -v people-context-data:/data ghcr.io/jinyangwang27/people-context:latest
```

该镜像以非 root 用户运行并将数据库保存在命名卷中，但它只是便捷的分发方式，并不是安全沙箱。
详细说明见 [Docker 文档（英文）](docs/docker.md)。

## 沟通辅助如何工作

服务器不会自己生成建议。它只向调用它的 AI 客户端提供有依据的结构化信息，例如：

- 对方的沟通偏好及其证据和可信度；
- 你们之间的关系、角色与近期互动摘要；
- 尚未完成的跟进事项；
- 由你自己设定的沟通原则。

客户端中的语言模型再结合你描述的具体情境，起草回复、协助会前准备、陪你练习对话或复盘交流。
如果姓名有歧义、记录不足或服务器无法连接，工作流应明确说明限制，并根据你当下提供的信息给出一般建议，
而不是猜测身份、性格或隐藏意图。

默认情况下，沟通辅助不会写入任何新记录。草稿不会自动发送；涉及承诺、日期、让步或事实的内容也不应由
Agent 凭空补充。完整原则和中英文示例见
[沟通辅助说明（英文）](docs/communication-guidance.md)与
[沟通场景示例（英文）](docs/communication-coaching-examples.md)。

## 隐私边界

| 它会做什么 | 它不会做什么 |
|---|---|
| 将资料保存在你拥有的本地 SQLite 文件中 | 将数据库或原始导入内容上传到服务端 |
| 按请求范围返回必要上下文 | 默认暴露敏感记录或完整导出 |
| 对写入、修改、合并和删除留下审计记录 | 在 `forget` 后保留可恢复的软删除副本 |
| 让导入和 Agent 提取的资料先经过人工审核 | 未经确认就把提取结果写入正式记录 |

SQLite 文件默认是明文文件；项目会在类 Unix 系统上以 `0600` 权限创建新数据库，但这并不等同于加密。
你可以配合全盘加密，或选择 SQLCipher 静态加密。

还需要注意：服务器本身不会上传数据，但 **AI 客户端是另一个信任边界**。如果你通过云端模型使用这些工具，
工具返回的姓名、关系、事实或互动摘要会像其他提示词内容一样发送给模型提供商，并受其数据政策约束。
详细说明见[隐私与安全文档（英文）](docs/privacy-and-safety.md)。

## 更多功能

- 关系图谱与联系人之间的连接路径；
- 待跟进事项、重要日期和长期未联系提醒；
- 邮件、mbox、vCard、日历、LinkedIn、Outlook 与 WhatsApp 导入；
- 可选的多语言语义搜索；
- Obsidian 只读视图、本地备份与设备迁移；
- Claude Code、Codex、Claude Desktop、OpenClaw 和通用 MCP 客户端支持。

完整的技术说明、架构、CLI 命令和接口契约以 [English README](README.md) 及其链接的英文文档为准。

## 参与贡献

欢迎提交 Issue 和 Pull Request。请使用虚构数据，不要公开真实联系人资料、原始导入内容、凭据或数据库。
具体要求见 [CONTRIBUTING.md](CONTRIBUTING.md)，问题与使用分享可以发到
[GitHub Discussions](https://github.com/JinyangWang27/people-context/discussions)。

如果 `people-context` 对你有帮助，点一个 Star 能让更多人发现它。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
