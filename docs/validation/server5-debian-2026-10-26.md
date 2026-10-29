# Debian / HNM provider 验收

日期：2026-10-26（Asia/Shanghai）。在用户授权的 `server5-vm-debian` 上，使用独立 Debian 12.15 容器、私有 cgroup/network/mount namespace 及原生 systemd 服务验证。只发布测试 API `192.168.1.41:18788`；不发布 FakeIP DNS/代理到宿主，不修改家庭路由或 HNM/DNS 配置。容器为透明套接字和 systemd 挂载测试具备额外权限，不把它当生产容器部署建议。

发行版本、基线/源摘要、OpenAPI、样例与安装包摘要见 [接入交接](hnm-provider-v1-handoff.md)。实际安装与最终复验使用 release ELF；早期调试构建仅用于发现启动差异。

通过的验证：

- Python/API/安装：Windows 和 Debian 各 15 项；OpenAPI 13 组确定性 HTTP 样例及其摘要校验通过。
- Rust workspace：881 passed、0 failed、5 ignored；locked 构建。两轮测试分别用于初次构建和最终候选源核对，计数不重复累加。Rust 1.99.1 有上游 fetch_update 弃用警告，不影响构建。
- observer 认证失败、禁止 URL、IPv6/指定能力拒绝；请求编号、未知/过期、真实零与缺测、空连接、探测失败/超时/上限由自动测试覆盖。
- 实际 FakeIP DNS 返回 `example.com → 7.80.78.144`。
- 网关本机健康探测代理 1079 访问 example.com，HTTP 200；API 的 HEAD probe 同样取得 HTTP 200。
- 独立临时客户端通过测试网关 DNS 获取上述 FakeIP，添加仅本客户端的 FakeIP 路由后，不使用 HTTP/SOCKS 代理，HTTPS 访问返回 HTTP 200（curl 默认 TLS 证书校验）。API 实际捕获同一客户端源地址、FakeIP、443、嗅探域名及 TProxy 类型的连接快照。
- 停止 API：核心 PID 不变、FakeIP DNS 仍响应。停止核心：API 503 CORE_UNAVAILABLE，不自动重启核心。
- 普通重启保留 instance UUID、更换 boot-id；修改实际配置导致 revision 改变，恢复后 revision 恢复。
- 其他 nftables sentinel 表和容器原 `/etc/hosts` 保留。停止后只清理 ppgw 表及登记的策略路由，没有全局 flush。
- 真实升级、卸载、重装：配置、UUID、observer Token、revision key 摘要逐项核对保持不变；其他防火墙表保持不变；项目程序确实被卸载，再安装后服务恢复。
- 原宿主 `hnm`、`hnm-agent` 仍 active，原 `paopaodns-hnm-p0` 容器仍 healthy。

机器可读结果：[核心/API](server5-debian-2026-10-26.json)、[独立客户端透明入口](server5-transparent-2026-10-26.json)。短连接记录是活动快照，不能视作完整历史。该流量是在 isolated free 模式下发起，API 仍不会因此全局报告 forwarding_verified=true。

覆盖缺口：本次只实际验证 free 模式、IPv4 与单个已知目标。socks5/yaml/suburl/OpenVPN、IPv6、UDP/QUIC、真实家庭 ROS/DNS 链路、长期负载及完整物理/虚拟机重启尚未验收；ARM64 没有构建实测。实际目标 IP、完整规则命中/回退/节点 ID、FakeIP 缓存精确查询和短连接缓冲仍为 unsupported/unavailable，保留 HNM 的联合诊断覆盖缺口。

实测修复：journald socket 不能通过 `/dev/stdout` 重开，改为直接 stdout 输出；iproute2 对不存在的路由表可能输出半截 `[`，按命令失败处理空结果；Debian Python 3.11 的 tarfile filter 参数不兼容，测试改为先检查路径/成员类型再展开。
