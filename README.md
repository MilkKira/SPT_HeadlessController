# SPT Headless Controller

AstrBot 插件：监听群聊中的 Headless 故障消息，例如 `A1卡了`、`W3卡了`，然后通过官方 Fika API 请求重启对应 Headless。

## 使用的 Fika API

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

1. `Fika Server 地址`：服务器根地址，例如 `http://192.168.1.10:6969`，不要附加 `fika/api`。
2. `Fika API Key`：Fika Server 配置中的 API Key。
3. `允许触发的群号`：建议填写实际使用的 QQ 群号。
4. `Headless 节点`：为 A1、W3 等节点分别填写名称和对应的 `profileId`。

节点配置示例：

- 节点名：`A1`
- profileId：`678012345678901234567890`
- 节点名：`W3`
- profileId：`678098765432109876543210`

`profileId` 是 Headless 的 MongoDB ObjectID，可以从 Fika WebApp 的 Headless 页面或 `GET /fika/api/headless` 响应中找到。

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
- API Key 只应配置在 AstrBot 插件设置中，不要发送到群聊。
- 默认启用 60 秒节点冷却，避免重复重启。
- 使用自签名 HTTPS 证书时，可关闭证书验证；普通 HTTP 或有效 HTTPS 证书不需要修改。
