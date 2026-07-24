# SPT Headless Controller 交接文档

## 1. 当前状态

- 项目类型：AstrBot Python 插件，用于通过 Fika API 重启指定 Headless。
- 当前版本：`v1.5.1`。
- 作者标识：`Mochix2Milk`。
- 当前基线提交：`10a4cba`（`Update logo`）。
- 目标接口基线：Project Fika 官方文档标注的 SPT 4.0 / Fika 2.0 API。
- Python 运行依赖：`aiohttp>=3.9,<4`。
- 当前发布包：`SPT_HeadlessController-v1.5.1.zip`。
- 发布包 SHA-256：`FC3ED094B89620D596F4AAD44B9018E9609C1543958DD4687E2E2BA0741B6488`。

发布包根目录包含：

```text
LICENSE
logo.png
main.py
metadata.yaml
README.md
requirements.txt
_conf_schema.json
```

`tests/`、缓存文件和旧版 ZIP 不应进入发布包。

## 2. 项目结构

| 文件 | 作用 |
| --- | --- |
| `main.py` | 插件注册、动态消息过滤、权限检查、冷却控制及 Fika API 调用 |
| `_conf_schema.json` | AstrBot WebUI 插件配置定义及默认值 |
| `metadata.yaml` | 插件名称、版本、作者、仓库等元数据 |
| `requirements.txt` | Python 运行依赖 |
| `tests/test_controller.py` | 不依赖真实 AstrBot/Fika 的配置和请求逻辑单元测试 |
| `README.md` | 用户安装、配置和故障排查说明 |
| `logo.png` | AstrBot 插件展示图标 |

本项目不是 BepInEx 客户端插件或 SPT 服务端模组，不修改游戏方法，也不包含 Harmony 补丁。它仅作为 AstrBot 与 Fika HTTP API 之间的控制器。

## 3. 运行流程

1. `HeadlessTriggerFilter` 在 AstrBot 唤醒阶段读取当前 `trigger_suffix`，只让形如“节点名 + 后缀”的消息进入处理器。
2. `on_message()` 检查群聊/私聊来源和发送者白名单。
3. 根据节点名或别名查找已启用节点；找不到时返回明确提示。
4. 校验 Fika 地址、API Key 和节点 `profileId`，静态配置错误不会进入冷却。
5. 使用节点级 `asyncio.Lock` 防止并发请求，并从首次网络检查开始记录冷却时间。
6. 通过 `event.send()` 主动发送“正在确认”进度消息。
7. 若 `verify_headless_online=true`，先请求 `GET /fika/api/headless` 并检查目标 `profileId`。
8. 请求 `POST /fika/api/restartheadless`，请求体为 `{"profileId": "..."}`。
9. 最终成功或失败消息作为该网络分支唯一的 `yield` 返回。
10. aiohttp 超时之外还有额外硬截止时间，避免处理永久停留在进度消息。

Fika 请求统一携带：

```http
Authorization: Bearer <API Key>
requestcompressed: 0
```

## 4. 配置语义

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `fika_server_url` | 空 | Fika 根地址；未写协议时自动补 `https://` |
| `fika_api_key` | 空 | Fika `fika.jsonc` 中的 API Key |
| `verify_headless_online` | `true` | 重启前检查目标是否在 Headless 列表中 |
| `verify_ssl` | `false` | SPT 默认使用自签名证书；使用受信任证书时建议开启 |
| `trigger_suffix` | `卡了` | 与节点名或别名组合成触发消息 |
| `allowed_group_ids` | `[]` | 空列表允许所有群聊，否则仅允许指定群 |
| `allowed_sender_ids` | `[]` | 空列表允许所有发送者，否则仅允许指定平台用户 ID |
| `allow_private_messages` | `false` | 开启后私聊不受群白名单限制，但仍受发送者白名单限制 |
| `request_timeout_seconds` | `10` | 单次 HTTP 请求总超时，运行时最小按 1 秒处理 |
| `cooldown_seconds` | `60` | 同一节点的请求冷却，运行时最小按 0 秒处理 |
| `nodes` | 空列表 | 节点名、别名、`profile_id` 和启用状态 |

### HTTPS 注意事项

- SPT/Fika 默认地址是 `https://<服务器 IP>:6969`。
- 用户只填写 `<服务器 IP>:6969` 时会自动补全 HTTPS。
- 明确填写 `http://` 时插件会保留该协议，并在状态检查中提示核对。
- Schema 默认值只影响新配置。旧版本已经保存的 `http://` 或 `verify_ssl=true` 不会自动迁移，升级后必须人工检查。
- 关闭证书验证适用于 SPT 默认自签名证书。若部署了有效证书，应开启验证以降低中间人攻击风险。

## 5. QQ 用户与群 ID

`allowed_sender_ids` 实际比较的是 `event.get_sender_id()` 返回的字符串，而不是强制要求纯数字。

- OneBot v11 / `aiocqhttp`（如 NapCat）：发送者 ID 通常就是 QQ 号，群 ID 通常就是 QQ 群号。
- QQ 官方机器人：发送者 ID 通常是平台分配的 UID/OpenID，不能通过官方接口转换为真实 QQ 号。
- QQ 官方机器人不要手工填写可见 QQ 号。应让用户在目标会话发送 `/sid`，复制输出中的 `UID` 填入 `allowed_sender_ids`。
- 不要把 `/sid` 输出中的 UMO 或 Session ID 当作发送者 UID。
- 不建议使用第三方 OpenID 转 QQ 服务，相关服务不是腾讯官方能力，存在隐私、稳定性和凭据风险。

## 6. 自检与部署

### 安装或升级

1. 在 AstrBot WebUI 上传 `SPT_HeadlessController-v1.5.1.zip`，或将包内文件放入对应插件目录。
2. 保存插件配置并重载插件。
3. 旧配置升级时，将 Fika 地址检查为 `https://<IP>:6969`。
4. 使用 SPT 默认自签名证书时，关闭“验证 HTTPS 证书”。
5. 在目标群发送 `/headless_status`，确认有效地址、节点、白名单和证书验证状态。
6. 发送 `A1卡了` 或实际配置的节点名与后缀进行验证。

`/headless_status` 只检查本地配置，不主动请求 Fika。真实连通性仍需通过一次实际触发验证。

### 正常预期

```text
收到，正在确认 A1 状态并请求重启……
A1 重启请求已成功提交。（Fika API HTTP 200）
```

### 常见失败

| 现象 | 检查项 |
| --- | --- |
| 停在“正在确认” | v1.5.1 会在 HTTP 超时外再等待约 2 秒后返回硬超时提示；检查地址、防火墙及 AstrBot 日志 |
| HTTPS 证书验证失败 | SPT 默认自签名证书场景关闭 `verify_ssl` |
| HTTP 401 | 检查 `Authorization: Bearer` 格式及服务器配置 |
| HTTP 403 | 检查 API Key |
| HTTP 404 | 检查接口版本、Headless 在线状态和基础地址是否误带 `fika/api` |
| 找不到 `profileId` | 从 Fika WebApp 或 `GET /fika/api/headless` 重新获取 |
| 消息完全不触发 | 检查插件是否加载、动态后缀、节点/别名、群和发送者白名单 |

## 7. 已完成验证

当前工作区验证命令：

```powershell
python -m py_compile main.py tests\test_controller.py
python -m unittest discover -s tests -v
```

截至交接时：

- Python 语法编译通过。
- 15 项单元测试全部通过。
- 覆盖动态后缀、中文/单值别名、群和发送者权限、私聊规则、数值容错、HTTPS 自动补全、显式 HTTP 保留、SSL 开关、在线检查开关、请求头/请求体、无效 URL、硬超时后的最终结果路径以及 Schema 一致性。
- 发布包清单、包内版本和作者信息已检查。

测试通过桩对象隔离 AstrBot 与 aiohttp 边界，因此不能代替真实 AstrBot + Fika 环境的集成测试。

## 8. 发布流程

发布新版本时至少同步修改：

1. `main.py` 中的 `PLUGIN_VERSION`。
2. `metadata.yaml` 中的 `version`。
3. 发布包文件名。
4. README/交接文档中的版本号、文件清单和 SHA-256。

PowerShell 打包示例：

```powershell
$releaseFiles = @(
    'LICENSE',
    'logo.png',
    'main.py',
    'metadata.yaml',
    'README.md',
    'requirements.txt',
    '_conf_schema.json'
)

Compress-Archive `
    -LiteralPath $releaseFiles `
    -DestinationPath 'SPT_HeadlessController-vX.Y.Z.zip' `
    -CompressionLevel Optimal
```

打包后执行：

```powershell
tar -tf .\SPT_HeadlessController-vX.Y.Z.zip
Get-FileHash -Algorithm SHA256 .\SPT_HeadlessController-vX.Y.Z.zip
```

## 9. 尚需真实环境确认

- AstrBot 当前生产版本加载 `CustomFilter` 和插件配置的实际表现。
- QQ 官方机器人下 `/sid` 返回 UID 与 `allowed_sender_ids` 的端到端匹配。
- SPT 4.0 / Fika 2.0 实例上的 Headless 列表响应结构。
- 自签名 HTTPS、有效 HTTPS 证书及显式 HTTP 反向代理三种部署方式。
- Fika 接收重启请求后 Headless 进程是否按预期重新上线。

## 10. 参考资料

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 消息事件](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)
- [AstrBot 内置 `/sid` 指令](https://docs.astrbot.app/use/command.html)
- [AstrBot OneBot v11 接入](https://docs.astrbot.app/platform/aiocqhttp.html)
- [Project Fika API](https://wiki.project-fika.com/advanced-features/fika-api)
- [Project Fika 服务器配置](https://wiki.project-fika.com/fika-configuration/server)
