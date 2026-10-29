# HNM API v1.1 接入交接

当前运行版本已更新至 `0.1.0-hnm-v1.20261027.1`，observer 根地址、UUID、Token 和 v1.1 契约不变。此次仅修复代理面板重复成员渲染并复用原生 PPsub 保存入口，见 [2026-10-27 修复记录](native-ppsub-2026-10-27.md)。以下保留前一版的完整证据及摘要。

日期：2026-10-26，Asia/Shanghai。第二批已补齐实际 FakeIP 缓存查询、有界 TCP 留存、模式派发证据和分阶段探测，已在独立 Debian 12.15/systemd 实测。HNM adapter、任务工具及页面仍由 HNM 侧接入。首批记录保留在 server5-debian 文档，本表指向当前版本。

| 项目 | 固定值 |
|---|---|
| API 根地址 | `http://192.168.1.41:18788` |
| 实例 UUID | `771563c8-b58b-4960-863c-0149cdeb52b4` |
| 发行版本 | `0.1.0-hnm-v1.20261026.2` |
| Git 基线 | `67600f73bb27c8bb6cc451069314060201c4f7e4`，包含本地未提交改造，不可单凭该提交复现 |
| 候选源摘要 | `c333782bff372e80eebac57cffe7da61e4494e11d758135100bf28cac56aafc2` |
| OpenAPI SHA256 | `a8411994f268b290cb528cddbec63870d5e25956768946c69d3d715b877989f9` |
| 样例 SHA256 | `46fc91d3b8cedc7e3ba3b26ce12d9ec41788f3bbde6d3bb8b38a7b24a303c2b9` |
| Debian 包 SHA256 | `fdd5a405e14f89d453d7b040d51fbffd233671c7ad377722f8ba499c58be3123` |

主机：Xterminal `server5-vm-debian`。容器名 `paopaogw-debian-lab`；其内部以原生 systemd 管理 `paopaogw` 和 `paopaogw-api`。Docker 仅提供隔离的 Debian、网络和测试边界，正式安装包不依赖 Docker。没有接管家庭主路由、生产 DNS 或现有 HNM 服务。

observer Token 仅保存在该测试实例 `/etc/paopaogw/read-token`，不要复制到 Git/浏览器/报告。源服务管理员通过服务器本地方式向 HNM 后端配置凭据；本次未把 Token 导出到开发机，也未修改 HNM 配置。API 账户不能调用旧管理路由、重启、关闭连接或切节点。当前只启用 `https://example.com/` 精确 URL 探测白名单。

接入顺序：读取 instance 绑定 UUID → capabilities 注册实际可用工具 → runtime/health/metrics 定时读取并保留 source time → 按需连接快照与 probe。core 503、stale、unknown、no_records 各自保留；不能把包装获取时间当原始采样时间。API 返回 boot-id 可识别核心重启；配置 revision 仅覆盖 sniffbox 实际加载配置，不能代表动态节点/订阅规则。

HNM 应重新读取 capabilities，按实际 mode 注册证据工具。现在 free/socks5/ovpn 可返回 decision.status=observed，但 rule_kind=mode_dispatch，只表示真实模式派发，不能包装成按域名命中的代理规则；yaml/suburl 的 mihomo 内部规则、最终节点仍 unavailable。连接缓冲仅覆盖嗅探后的 TCP，最多 1024 条、保留 300 秒，complete_history=false。HNM 不能仅因端点可用就全局开启完整 gateway_decisions，必须适配上述 scope 与 reason。

新调用：GET `/api/v1/fakeip/lookup?domain=example.com`（或 ip）；GET `/api/v1/connections?view=recent` / `view=observed_active`；GET `/api/v1/decisions`。recent 支持 domain/client/since/until 和 boot/filter 绑定的 cursor，核心重启旧 cursor 返回 409。原 connections 默认 snapshot 保留首批格式与未知源年龄。

probe 支持 TLS/HTTP 分阶段结果及耗时，使用同一个 loopback 源端口与核心连接精确关联。free/ovpn 可取得实际 peer，SOCKS/mihomo 不把上游代理地址冒充实际目标。完整 DNS 回答、指定节点、固定 DNS 地址、强制地址族、UDP/QUIC 未提供；probe 仅使用当前出站，不切换全局节点。runtime.last_probe 是最近一次结果，有独立时间与 stale，不代表全局转发通过。

OpenAPI、样例与字段说明分别见 `docs/openapi-v1.json`、`integration/fixtures/` 和 `docs/integration-api-v1.md`。确定性样例来自实际 HTTP handler + 合成核心，不是网络实测；真实证据见同目录 server5 JSON。

本轮验收：Python 25 passed；Rust 887 passed、5 ignored；20 组确定性响应。真实 FakeIP 未命中不创建记录、正反查对应 DNS、短连接留存、实际连接失败、精确探测关联、游标筛选绑定/重启失效均通过。证据见 `server5-evidence-2026-10-26.json`，原部署隔离回归和独立透明入口分别见 `server5-evidence-regression-2026-10-26.json`、`server5-evidence-transparent-2026-10-26.json`。

测试实例保持运行，不自动接管任何真实网络。若容器被手动关闭，可在 server5 执行 `docker start paopaogw-debian-lab`，再在容器内启动上述两个服务。构建缓存与 release 包保存在 server5 `/opt/paopaogw-lab/`。

面板与订阅导入入口已另行开放，地址、observer 凭据读取位置和接入边界见 [前端及 HNM 接入信息](subscription-frontend-2026-10-26.md)。管理密码没有写入文档或 Git；由所有者在当前会话取得。
