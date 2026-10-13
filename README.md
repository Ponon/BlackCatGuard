<div align="center">
  <img src="./static/images/logo.svg" width="96" height="96" alt="黑猫群管 Logo">
  <h1>黑猫群管 · BlackCatGuard</h1>
  <p>一个机器人，一个群，让群管理更简单</p>
  <p>入群验证 · 欢迎消息 · 广告拦截 · 循环广播 · 事件推送</p>
  <p>
    <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-24292f?style=flat-square" alt="MIT License"></a>
    <a href="https://github.com/Ponon/BlackCatGuard/stargazers"><img src="https://img.shields.io/github/stars/Ponon/BlackCatGuard?style=flat-square&amp;color=24292f" alt="GitHub Stars"></a>
    <a href="https://github.com/Ponon/BlackCatGuard/forks"><img src="https://img.shields.io/github/forks/Ponon/BlackCatGuard?style=flat-square&amp;color=24292f" alt="GitHub Forks"></a>
    <img src="https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.13">
    <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&amp;logo=fastapi&amp;logoColor=white" alt="FastAPI">
    <img src="https://img.shields.io/badge/SQLite-003B57?style=flat-square&amp;logo=sqlite&amp;logoColor=white" alt="SQLite">
  </p>
  <p>
    <a href="#功能介绍">功能介绍</a> ·
    <a href="#快速开始">快速开始</a> ·
    <a href="#使用说明">使用说明</a> ·
    <a href="#特别感谢">特别感谢</a> ·
    <a href="#star-趋势">Star 趋势</a>
  </p>
</div>

## 项目介绍

黑猫群管是一个可自行部署的 Telegram 群管理工具，通过网页配置机器人和群组，统一管理入群验证、欢迎消息、广告拦截与定时广播。

项目采用 **FastAPI + Tortoise ORM + SQLite + Jinja2**，页面使用原生 HTML、CSS 和 JavaScript，无需额外构建前端。当前采用单机器人、单群组设计，配置和运行数据保存在本机。

后台服务独立运行，关闭网页后仍会接收群事件、执行广播和监测连接。核心流程已有自动化测试，真实 Telegram、AI 与 Bark 环境仍需完成联调验证。

如果这个项目对你有帮助，欢迎点一个 **Star**，也欢迎通过 [Issues](https://github.com/Ponon/BlackCatGuard/issues) 提交问题和建议。

## 功能介绍

| 模块 | 功能 |
| --- | --- |
| 工作台 | 实时连接状态、请求延迟与动态指示点、今日新增与拦截统计、最新 5 条事件 |
| 群管理 | 配置 Bot Token 与群 ID、保存后自动检测机器人连接和管理员权限 |
| 入群验证 | 按钮验证、随机算术题，支持验证超时及回答错误处置 |
| 入群欢迎 | 独立开关、自定义欢迎语、昵称和用户名变量、延时删除 |
| 广告拦截 | 关键词包含匹配、OpenAI 兼容 AI 识别、用户白名单、消息与成员处置 |
| 群广播 | 保存多条内容、配置链接按钮、每天多个时间点或按分钟间隔发送 |
| 事件日志 | 按时间查看事件、昵称、用户名、内容与结果，分页及确认清空 |
| 系统设置 | 统一 HTTP/HTTPS 代理、修改管理员账号、Bark 推送及事件选择 |

### 入群与防护流程

- 开启验证：收到入群事件 → 限制文字和媒体发送 → 发送验证消息 → 验证通过 → 恢复群组默认权限 → 删除验证消息 → 按需发送欢迎语
- 关闭验证、开启欢迎语：收到入群事件后直接发送欢迎语
- 回答错误：可选择 **无、踢出用户、限制用户所有权限**；选择无时不额外处罚，保留待验证状态和原倒计时
- 验证超时：按设置踢出成员或限制权限；执行失败会记录原因并延后重试
- 广告判断：白名单优先放行，关键词和 AI 可同时启用，任一命中即判定为垃圾消息；关键词命中后跳过 AI 调用

## 快速开始

### 环境准备

- Python **3.13**
- 能访问 Telegram 的网络，或可用的 HTTP/HTTPS 代理
- 一个通过 [BotFather](https://t.me/BotFather) 创建的机器人
- 一个 Telegram 超级群组，并将机器人设置为管理员

以下命令适用于 macOS / Linux：

```bash
git clone https://github.com/Ponon/BlackCatGuard.git
cd BlackCatGuard

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 1777 --workers 1
```

打开 **<http://127.0.0.1:1777>**，首次访问时创建管理员账号。

### 首次配置

1. 在 **群管理** 填写 Bot Token 和负数群 ID，例如 `-1001234567890`
2. 授予机器人删除消息、限制成员及邀请成员等所需管理员权限
3. 如需代理，先到 **系统设置** 保存代理地址，再检测群配置
4. 检测通过后，按需配置并启用入群验证、欢迎语和广告拦截
5. 在 **群广播** 新建内容、保存循环规则，再从任务列表开启广播

正式运行时使用一个应用实例和一个 worker，不启用 `--reload`。需要公网访问时，可在前面配置 HTTPS 反向代理；上述命令默认只监听本机。

## 使用说明

### 入群欢迎语

欢迎语独立于验证开关，支持以下变量：

| 变量 | 含义 |
| --- | --- |
| `{nickname}` | 成员昵称 |
| `{username}` | 带 `@` 的用户名；成员没有用户名时使用昵称 |

```text
欢迎 {nickname}（{username}）加入群组！
```

启用验证时，只有验证成功且验证消息删除后才发送欢迎语。欢迎语删除延时设置为 `0` 时保留消息。

### 广告拦截

关键词每行填写一个，采用包含匹配，英文不区分大小写。AI 支持 OpenAI 兼容接口，可填写第三方服务或本地模型的基础地址、模型名称及 API Key。

白名单支持用户数字 ID 或 `@用户名`。消息测试使用已保存的配置，无需开启规则或绑定群组；配置完整的 AI 会参与测试，可能产生接口费用。测试只进行判断，不删除消息或处罚成员。

命中垃圾消息后，消息处理可选择删除当前消息或清理该用户已接收的可删除消息；成员处理可选择限制权限或踢出，同时选择时优先踢出。

### 循环广播

先保存广播内容，再创建绑定该内容的循环任务：

- **每天发送**：设置一个或多个时间点，使用北京时间
- **间隔发送**：按指定分钟数循环，从启用任务后开始计时
- **时间变量**：正文中的 `{datetime}` 会替换为实际发送时的北京时间
- **链接按钮**：自定义按钮名称、链接和所在行

左侧负责新建、编辑，右侧展示已保存内容与任务；任务开关位于任务列表。欢迎语与广播均按普通文本发送。

### 系统推送

推送方式选择 **Bark**，填写包含设备 Key 的完整推送地址，再选择需要通知的事件并保存。支持广告拦截、验证通过、验证超时、验证答错、广播失败、连接异常、连接恢复、自动停用和运行异常等事件。

支持使用 [Bark](https://github.com/Finb/Bark) 默认服务或自建服务器，也可单独发送测试推送检查配置。

## 连接监测与故障恢复

后台每约 **10 秒**检测一次 Telegram 连接与机器人权限，首页每 10 秒读取后台结果，不额外发起 Telegram 检测请求。

| 状态 | 展示与处理 |
| --- | --- |
| 连接正常 | 绿色动态指示点，显示本轮 Telegram 请求往返耗时 |
| 延迟 ≥ 1000 ms | 红色动态指示点，提示延迟较高；高延迟本身不触发停用 |
| 连续失败 1～2 次 | 黄色动态指示点，保留现有功能设置 |
| 连续失败 3 次 | 自动关闭入群验证、欢迎语、广告拦截和广播任务，记录原因并按配置推送 |
| 状态超过 45 秒未更新 | 暂停执行，清空旧延迟，避免继续展示过期的正常状态 |
| 连接恢复 | 继续监测，已经关闭的功能需要手动重新开启 |

配置了代理时，请求延迟包含当前代理链路。留空代理地址使用默认网络；填写后，Telegram、AI 和 Bark 请求统一使用该代理。`127.0.0.1` 指后台所在机器，容器环境下指容器自身。

删除及权限操作保存处理进度，遇到可重试的网络错误会延后重试并遵守限流等待时间。发送结果不确定时不自动重发，避免重复广播、欢迎或推送；错过的历史广播不集中补发。

更换机器人或目标群、清除群配置时，会关闭关联功能与广播任务，保留功能配置内容，旧入群会话停止执行。重新保存相同的群配置不会关闭已有开关。

## 使用边界

- 验证限制在收到 Telegram 入群事件后执行，事件到达前可能存在短暂发言窗口
- 删除用户全部消息只能清理机器人已接收、且发送未满 48 小时的可删除消息，无法读取完整群历史
- 踢出通过短时封禁后解除封禁实现，允许重新加入；超级群移除操作可能同时清理该用户消息
- AI 判断失败会记录异常，不将未知结果当成垃圾消息删除
- 今日统计只包含当前机器人和目标群实际接收到的事件，消息测试不参与统计，清空日志不清空统计
- 同一机器人已有 Webhook 或其他更新接收程序时会冲突，应先停止原接收服务

## 数据与备份

运行数据位于项目的 `data/` 目录：

```text
data/
├── blackcatguard.db    # 配置、任务、事件日志与运行进度
├── session.key         # 登录会话签名密钥
└── *.lock              # 后台服务文件锁
```

启动时自动创建数据库并补齐新增表和字段。更新项目或迁移服务器前，建议先停止应用，再备份整个 `data/` 目录；恢复后重新启动即可。

该目录包含机器人、AI、推送等敏感配置，已在 `.gitignore` 中忽略，请勿上传至公开仓库或随 Issue 附带。

## 开发与贡献

欢迎通过 [Issues](https://github.com/Ponon/BlackCatGuard/issues) 提交问题，通过 [Pull Requests](https://github.com/Ponon/BlackCatGuard/pulls) 贡献代码。反馈问题时请附上复现步骤和脱敏后的错误信息。

```text
├── dao/          # 数据模型与数据库升级
├── routers/      # 页面及配置接口
├── services/     # Telegram、验证、拦截、广播和推送服务
├── templates/    # Jinja2 页面模板
├── static/       # 样式、脚本与图标
├── tests/        # 自动化测试
├── utils/        # 安全、时间与模板工具
└── main.py       # 应用入口与后台服务生命周期
```

测试使用内存或临时数据库与模拟网络，不向真实群发送消息，也不调用真实 AI、Bark 服务。JavaScript 检查需要本机安装 Node.js：

```bash
.venv/bin/python -m unittest discover -s tests -q
node tests/test_form_state.js
node tests/test_toast.js
node tests/test_dashboard_live.js
```

## 特别感谢

<table>
  <tr>
    <td align="center" width="140">
      <a href="https://github.com/Barryliu">
        <img src="https://github.com/Barryliu.png?size=160" width="80" height="80" alt="Barryliu 的 GitHub 头像"><br>
        <strong>Barryliu</strong>
      </a>
    </td>
  </tr>
</table>

也感谢以下开源项目及其维护者：

[FastAPI](https://github.com/fastapi/fastapi) · [Tortoise ORM](https://github.com/tortoise/tortoise-orm) · [Jinja](https://github.com/pallets/jinja) · [Bark](https://github.com/Finb/Bark) · [Star History](https://github.com/star-history/star-history)

## Star 趋势

感谢每一位关注和支持黑猫群管的朋友。

<a href="https://www.star-history.com/?repos=Ponon%2FBlackCatGuard&amp;type=date&amp;legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Ponon%2FBlackCatGuard&amp;type=date&amp;theme=dark&amp;legend=top-left">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Ponon%2FBlackCatGuard&amp;type=date&amp;legend=top-left">
    <img alt="BlackCatGuard Star 增长趋势" src="https://api.star-history.com/chart?repos=Ponon%2FBlackCatGuard&amp;type=date&amp;legend=top-left" width="100%">
  </picture>
</a>

图表由 [Star History](https://www.star-history.com/blog/how-to-use-github-star-history/#how-to-embed-the-chart-in-your-readme) 提供，读取公开仓库的实际数据；展示情况取决于仓库公开状态和图表服务可用性。点击图表可打开趋势详情。

## 许可证

本项目采用 [MIT License](./LICENSE) 开源，版权归 Ponon 所有。使用、修改或分发时，请保留版权声明和许可证全文。
