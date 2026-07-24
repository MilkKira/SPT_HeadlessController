# SPT Headless Controller

AstrBot 插件：使用不受 AstrBot `wake_prefix` 限制的自定义过滤器监听群聊消息，例如 `A1卡了`、`W3卡了`，然后通过官方 Fika API 请求重启对应 Headless。触发后缀、节点名和节点别名均从插件配置动态读取。

## 使用的 Fika API

接口约定已对照 [Project Fika 官方 API 文档](https://wiki.project-fika.com/advanced-features/fika-api)；该文档当前标注适用于 SPT 4.0 / Fika 2.0。

插件按照 Fika API 和 Fika WebApp 的实际实现调用：

```http
GET /fika/api/headless
Authorization: Bearer <API Key>
requestcompressed: 0
```

重启前通过此接口确认目标 `profileId` 当前存在。

```http
POST /fika/api/restartheadless
Authorization: Bearer <API Key>
requestcompressed: 0
Content-Type: application/json

{
  "profileId": "Headless 的 MongoDB ObjectID"
}
```

Restart 接口返回 HTTP `200` 时，插件确认重启请求已成功提交。HTTP `401`、`403`、`404` 会分别提示鉴权格式、API Key 或 Headless 在线状态问题。

## 配置

安装插件后，在 AstrBot WebUI 的插件配置中填写：

| 配置项 | 默认值 | 实际作用 |
| --- | --- | --- |
| `Fika Server 地址` | 空 | Fika Server 根地址，例如 `https://192.168.1.10:6969`；省略协议时自动补 `https://`，不要附加 `fika/api` |
| `Fika API Key` | 空 | 通过 `Authorization: Bearer <API Key>` 发送 |
| `重启前确认 Headless 在线` | 开启 | 调用重启接口前，先确认目标 `profileId` 在 Headless 列表中 |
| `验证 HTTPS 证书` | 关闭 | SPT 默认使用自签名证书；服务器配置受信任证书后再开启验证 |
| `触发消息后缀` | `卡了` | 与节点名或别名动态组合，例如改成 `掉线` 后使用 `A1掉线` |
| `允许触发的群号` | 空列表 | 留空允许所有群聊；填写后仅允许指定群聊 |
| `允许触发的用户 ID` | 空列表 | 留空允许群内所有成员；填写后群聊和私聊都仅允许指定用户 |
| `允许私聊触发` | 关闭 | 开启后允许私聊；私聊不受群号白名单限制，仍受用户白名单限制 |
| `Fika API 请求超时秒数` | `10` | 限制单次列表或重启请求的总等待时间，最小按 1 秒处理 |
| `同一节点重启冷却秒数` | `60` | 从网络请求开始计时，防止超时或重复消息造成连续重启 |
| `Headless 节点` | 空列表 | 配置节点名、可选别名、`profileId` 和启用状态 |

消息发送者或所在群聊不在已配置的白名单内时，插件会停止处理并回复：`您的权限不足，无法重启`。

节点配置示例：

- 节点名：`A1`
- profileId：`678012345678901234567890`
- 节点名：`W3`
- profileId：`678098765432109876543210`

`profileId` 是 Headless 的 MongoDB ObjectID，可以从 Fika WebApp 的 Headless 页面或 `GET /fika/api/headless` 响应中找到。

## 首次调用与自检

配置并重载插件后，先在 QQ 群内发送：

```text
/headless_status
```

插件会显示 Fika Server、自动补全后的有效地址、HTTPS 证书验证状态、API Key、触发后缀、在线检查开关、Headless 节点以及当前会话是否允许触发，并列出空值、无效地址、重复节点标识和非法数值等配置警告。确认 A1 显示为“profileId已配置”且当前会话允许触发后，再发送：

```text
A1卡了
```

如果消息格式正确但没有配置 A1，插件会明确回复节点未配置，不再静默忽略。修改 `触发消息后缀` 后，请使用新后缀测试，例如配置为 `掉线` 时发送 `A1掉线`。若 `/headless_status` 也完全没有回复，请在 AstrBot 插件管理中确认插件已启用并完成重载。

### HTTPS 与无响应排查

- SPT/Fika 默认地址为 `https://<服务器 IP>:6969`。只填写 `<服务器 IP>:6969` 时，插件会自动补全 HTTPS。
- 若配置中明确写了 `http://`，插件会尊重该设置，但 `/headless_status` 会提示核对；没有 HTTP 反向代理时应改为 HTTPS。
- SPT 默认加载自签名证书，新安装默认关闭证书验证。旧配置升级后若仍为开启状态，请手动关闭，否则插件会返回证书验证失败提示。
- 网络调用除 aiohttp 自身超时外还有额外硬截止时间；到期后会回复超时原因，不会一直停留在“正在确认”。

## 使用

在允许的群聊内发送：

```text
A1卡了
```

插件处理流程：

1. 匹配 A1 节点配置。
2. 请求 `GET /fika/api/headless`，确认 A1 的 `profileId` 存在。
3. 请求 `POST /fika/api/restartheadless`。
4. Fika 返回 HTTP `200` 后回复：

```text
A1 重启请求已成功提交。（Fika API HTTP 200）
```

## 安全设置

- 建议同时配置群号和管理员 QQ 用户白名单。
- 未通过群白名单或用户白名单的重启触发会回复“您的权限不足，无法重启”，且不会调用 Fika API。
- API Key 只应配置在 AstrBot 插件设置中，不要发送到群聊。
- 默认启用 60 秒节点冷却，避免重复重启。
- SPT/Fika 默认使用 HTTPS 和自签名证书，因此插件默认关闭证书验证；若服务器改用了受信任证书，建议开启验证。
