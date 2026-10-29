# 前端访问与 HNM 接入信息

> 以下为 2026-10-26 的历史部署记录。2026-10-27 已改为原生 PPsub 内直接保存、应用，独立导入页撤销；最新入口与验收见 [原生 PPsub 修复记录](native-ppsub-2026-10-27.md)。

2026-10-26，Asia/Shanghai。当前核心及 observer API 为 `0.1.0-hnm-v1.20261026.2`，部署于 `server5-vm-debian`（192.168.1.41）内的独立 Debian Docker 测试实例 `paopaogw-debian-lab`；不是宿主机原生安装，也不是新建的独立 VM。

| 用途 | 地址 |
|---|---|
| 订阅导入与应用 | http://192.168.1.41:18781/ |
| 原生运行面板 | http://192.168.1.41:18780/ui |
| HNM observer API 根地址 | http://192.168.1.41:18788 |
| 实例 UUID | `771563c8-b58b-4960-863c-0149cdeb52b4` |
| API 契约 | `D:/workspace/paopaogw/docs/openapi-v1.json`（v1.1） |
| HNM 契约包 | `D:/workspace/paopaogw/dist/hnm-provider-v1.1-contract.zip` |

observer API 继续使用独立只读 Token；它只在容器内 `/etc/paopaogw/read-token`。HNM 同机测试时可由后端管理员在 server5 本地读取并注入 HNM 服务端配置：

```sh
docker exec paopaogw-debian-lab cat /etc/paopaogw/read-token
```

请求使用 `Authorization: Bearer <token>`。不要用面板管理密码/摘要替代 observer Token。上述命令供服务端接入使用，凭据不应贴入 Git、报告或浏览器。Token、UUID 未因开放面板而改变。

HNM 先读取 `/api/v1/instance` 与 `/api/v1/capabilities`，再接 runtime/health/metrics；按需调用 fakeip/lookup、connections 的 recent/observed_active、decisions 和白名单 probe。默认探测白名单仅有 `https://example.com/`。当前测试仍为 free 模式；用户导入真实订阅后模式会变化，应重新获取能力。yaml/suburl 的 mihomo 内部规则/最终节点仍 unavailable，不能全局宣称完整 gateway_decisions。详细语义见 [交接](hnm-provider-v1-handoff.md)。

本次另加人类管理员订阅服务 `paopaogw-subscription.service`，容器内端口 8789。支持 Clash/Mihomo URL、YAML 文件、PPsub JSON 导出文件；选择“保存并应用”才写配置并重启测试核心。保存前检查 base revision，加载失败在无外部修改时恢复原配置；订阅地址不回显、不写请求日志。原 PPsub 编辑器仅导出配置，导出后可在新页面以 JSON 方式导入。

已安装官方 `MetaCubeX/mihomo` v1.19.33 amd64-compatible，压缩包 SHA256 `386508bfb1c72c43b6a0eda73708ecdce82546581b0ebcb16366c53757c622b6`，以及按官方 GitHub release digest 校验的 GeoIP/GeoSite/Country/ASN 数据。mihomo 仅为准备订阅模式安装，当前未填写任何真实订阅。

宿主增加两个专用 socket proxy：`paopaogw-lab-panel.socket`（18780→容器80）与 `paopaogw-lab-subscription.socket`（18781→容器8789）。它们指向当时容器 IP；如果重新创建容器/IP 变化，应更新代理单元。没有修改家庭主路由、现有 HNM 或 DNS 配置。核心/API 发布包保持 .2，订阅管理是本次独立部署的新增组件，尚未生成新的整体发布包。

验证：本地 29 项 Python 测试通过；浏览器登录订阅页面、URL/YAML 输入、桌面/手机布局通过，无页面异常；订阅页/运行面板均 HTTP 200，observer 未带 Token 为 401。没有执行真实订阅提交。“虚拟配置导入并重启”实测被自动审批拒绝，理由是本次用户授权启动前端，因此改为只读页面检查，保存/应用等待用户从页面操作。
