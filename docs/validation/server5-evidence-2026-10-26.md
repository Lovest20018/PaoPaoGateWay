# 第二批实际证据验收

2026-10-26（Asia/Shanghai）。版本 `0.1.0-hnm-v1.20261026.2`，原 server5 独立 Debian 12.15/systemd 实例升级，保留 UUID、凭据和配置。候选/契约/包摘要见 [交接](hnm-provider-v1-handoff.md)。测试实例 API 仍为 `192.168.1.41:18788`，没有修改 HNM 代码或服务配置。

- Python：Windows 与 Debian 各 25 项通过；OpenAPI 3.1 修订 1.1 校验 20 组确定性 handler 样例。
- Rust：887 passed、5 ignored；新增缓存观测不分配/不提升 LRU、真实碰撞后的地址对应、短连接完成/失败、容量/时间淘汰、mihomo unavailable 测试。首轮 Windows CRLF 上传导致两个上游源码字符串断言失败，规范化 Linux 构建输入后全量通过；没有跳过失败测试。
- FakeIP：未知域名连续查询均 no_records；通过真实 DNS 协议分配后，domain/ip 两个方向与 DNS 回答相符。lookup 不制造新地址。
- probe：HEAD `https://example.com/` 返回 HTTP 200，TCP connected、TLS completed（默认 CA/SNI）、HTTP completed；获得同一核心连接 ID、实际目标地址及 direct 出站。DNS 投影记录选中地址/核心 resolver 来源，complete_answers=false。
- 短连接：探测完成后 recent 仍能读取结束时间、字节数、config_revision、mode:free / free_mode_no_rule、connect_result=connected；application_result=unknown，不能以 TCP 建连推断业务成功。
- 实际连接失败：通过测试专用 loopback 代理访问本实例未监听端口，record 明确 outbound_connect/failed；没有把异常原文返回 API。
- cursor：原条件继续读取可用，改条件拒绝，核心重启后旧 cursor=409 CURSOR_EXPIRED，新缓冲为空。实例 UUID 保留、boot-id 更换。
- 上一轮故障隔离/资源清理回归通过：停 API 核心继续，停核心 API 503，hosts 和其他 nft 表保留，配置 revision 修改与恢复匹配。
- 独立测试客户端通过 FakeIP 路由到透明入口，HTTPS HTTP 200 且抓到 TProxy 连接，原家庭网络及 HNM/DNS 服务保持原状态。

机器证据：[新接口](server5-evidence-2026-10-26.json)、[部署回归](server5-evidence-regression-2026-10-26.json)、[透明入口](server5-evidence-transparent-2026-10-26.json)。确定性样例不属于真实流量证据。

边界：实际只验收 free/IPv4；SOCKS/OpenVPN/mihomo、IPv6/UDP/QUIC、真实家庭 ROS/DNS 尚未验收。mode_dispatch 是真实模式选择，非域名规则系统；mihomo 节点/规则、完整 DNS 回答、核心崩溃时尚未关闭的流量、嗅探前拒绝以及 UDP 仍无完整证据。后续受控配置写入仍未开放，observer 不能改网络。
