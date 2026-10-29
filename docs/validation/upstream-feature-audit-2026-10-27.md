# 与上游 Docker/ISO 交付的功能差异审计

日期：2026-10-27，Asia/Shanghai。只读核对源码、上游 GitHub 元数据、当前运行实例及已有验收记录；本轮未修改网关配置或启用功能。

## 对比基准

- 官方上游：<https://github.com/kkkgo/PaoPaoGateWay>；用户 fork：<https://github.com/Lovest20018/PaoPaoGateWay>。
- 审计时上游 main 与本地基线均为 `67600f73bb27c8bb6cc451069314060201c4f7e4`，上游提交日期为 2026-09-14。此处对比源码，未核验镜像仓库各 tag 的实际 digest 或二进制版本。
- 上游仓库的 Dockerfile 入口为 `/remakeiso.sh`，主要是 `sliamb/ppgwiso` ISO 定制工具。上游运行环境是由 ISO 启动的系统。当前 fork 改成 Debian 原生 systemd 服务，测试时用 Debian 容器隔离；它不是一个新的正式 Docker 网关发行版。
- 当前核心/API 为 `0.1.0-hnm-v1.20261027.1`，另已部署 PPsub 当前配置读取和策略编辑补丁。当前运行 suburl + mihomo v1.19.33；读取到 29 个组、17 条规则、16 个规则订阅、45 个内嵌节点。

上游来源：[README](https://github.com/kkkgo/PaoPaoGateWay/blob/67600f73bb27c8bb6cc451069314060201c4f7e4/ReadMe.md)、[Dockerfile](https://github.com/kkkgo/PaoPaoGateWay/blob/67600f73bb27c8bb6cc451069314060201c4f7e4/Dockerfile)。

## 已保留或已补齐的功能

| 功能 | 当前判断 | 证据与限制 |
|---|---|---|
| Rust sniffbox、FakeIP DNS、嗅探和 TCP 透明入口 | 已保留并有实测 | free/IPv4 下独立客户端经 FakeIP 路由访问 HTTPS 返回 200，捕获 TProxy 源地址和域名；不是家庭全链路验收 |
| free、socks5、yaml、suburl、ovpn 五模式 | 核心代码保留 | 各模式实测范围不同，见下表；不能把代码存在等同于全部可交付 |
| mihomo 代理、规则、连接、测速等原生面板/API | 已保留 | 真实订阅代理列表已验证；重复组成员导致的面板异常已修复 |
| 订阅下载、subtime/subcron、fast_node、DNS burn | 原流程保留 | 当前并未全部启用或做长期/异常专项测试；调度时区另有缺口 |
| 原生 PPsub 多订阅、节点组、规则、JSON 导入导出 | 保留并增加保存能力 | 原生 JSON 路径仍使用上游生成器；高级模式未逐一做 Debian 运行验收 |
| 完整 Clash 配置在 PPsub 中读取、编辑、保存 | 新增实现 | 实际组/规则读取，保留未展示字段，快照冲突检查，mihomo 校验，失败回滚；存在明确编辑范围限制 |
| 本地策略随订阅更新重新合并 | 新增实现 | 隔离实例验证节点变化后策略仍生效；复杂冲突管理尚未补齐 |
| net_rec 流量记录/分布、日志、ACL、显式代理认证、Geo 更新 | 代码保留 | 关闭状态或缺少专项验收不应算成已删除；控制台日志来源需 Debian 适配 |
| HNM observer v1.1 | fork 新增 | 身份、能力、状态、指标、FakeIP 查询、有界 TCP 历史、分阶段探测；不是上游本来就有的 HNM 接口 |

基础和透明入口证据见 `server5-debian-2026-10-26.md`、`server5-evidence-2026-10-26.md`、`server5-evidence-transparent-2026-10-26.json`；PPsub 保存及回滚证据见 `ppsub-policy-edit-2026-10-27.md`。

## 明确未补齐的差异

| 项目 | 当前缺口 | 应补方式 | 性质 |
|---|---|---|---|
| Cloudflared | Debian launcher 明确拒绝非空 token；当前实例也无二进制。上游直连/代理双副本、协议评分、重启及面板状态虽有代码，但在本部署不可用 | 适配 Debian 包/二进制、用户和目录权限、UID/nft 分流、进程生命周期、双副本及 QUIC/HTTP2 验收，再解除启动限制 | 确定未支持，可实现 |
| OpenVPN | 出站/监管代码保留，当前没安装 openvpn，无真实隧道验收 | 补依赖、配置/证书/TUN/路由预检、实际隧道与重连测试 | 缺依赖与验收，不是缺整个核心 |
| sniffbox 热重载 | Debian 下 SIGHUP 被拒绝，原 hot_reload_box 也改为 cold_reload_box。配置应用需要冷重载/服务重启，相关内存连接/统计可能被清空 | 使有效配置快照、revision 和热重载原子一致，再恢复支持的参数热更新；mihomo 自身 YAML 重载应另行区分 | 相对上游的行为退化，主动限制 |
| 完整 Clash→PPsub 高级编辑 | 目前支持组成员、名称、手选/测速/fallback、过滤、规则和规则订阅；不转换智能测速、前置代理改动、PPsub 全局健康监测、定时重启和 Shadowsocks 入站；不能在此模式新增/删除/更名源订阅，也不支持 PPsub 的 URL 下载规则块 | 扩展明确的 Clash 原生字段编辑和控制接口；PPsub 专有功能需走它自己的生成/监督逻辑，或另补相应模块 | 编辑桥接缺口，原生 PPsub 的对应代码并未删掉 |
| 本地策略与远端订阅冲突管理 | 已有手动保存的预校验和回滚；订阅日后删节点/改名称时，覆盖组仍可能引用旧节点。缺少冲突预览、策略撤销/清空、自动更新失败的持久回退状态 | 在自动刷新链路做候选合并/校验/切换，保留最后成功版本；提供冲突详情、恢复上游和删除覆盖入口 | 新覆盖机制仍需补运维闭环 |
| mihomo/Geo 的一体化安装与维护 | 上游 ISO 构建可带兼容/v3/ARM64 核心与 Geo；Debian 包不附 mihomo/Geo。当前实例是另行安装的，不能代表新机器开箱即用 | 版本锁定、架构选择、下载与校验、升级/回退、离线依赖包；Geo 的现有运行时更新代码可继续复用 | 发行和依赖管理缺口 |
| 定时任务时区 | 当前实例实际 UTC；subcron 和 PPsub restart_cron 用系统本地时间，未统一用户的 Asia/Shanghai 语义 | 指定服务时区或显式配置调度时区，并展示下次运行时间 | 已确认环境差异；不能误称全部定时功能不存在 |
| 启动屏与管理日志 | 上游依赖虚拟控制台；Debian 控制器输出改到专用日志/标准输出，web screen 仍指向 `/dev/vcs1`。设备存在也不能证明页面展示的是该实例的控制器日志 | 接本服务 journald 或专用日志流，区分核心、控制器和 mihomo；核心日志流不应一概算失效 | 日志来源适配缺口 |
| 上游自动网络初始化与调优 | Debian 不自动接管 IP/DHCP、默认路由、NTP、全局 sysctl 和网卡调优；默认只配置指定网卡与本服务拥有的 nft/策略路由 | 主机部署预检、明确的可选接口级调优；网络/时间交由 Debian 服务管理 | 宿主职责变化，不宜直接照搬 ISO 启动脚本 |
| 远程 ppgw.ini 下发 | 当前 launcher 要求本地 INI，并将其放入运行命名空间；默认本地优先。旧 ppgwurl.ini/paopao.dns:7889 等分支仍在，但没有形成受支持、经验证的 Debian 远程配置链路 | 若继续需要集中下发，补拉取代理、认证、来源优先级、版本与回滚；不能仅凭遗留分支声称完整兼容 | 默认部署模式变化，旧代码未等于完整移植 |
| ARM64 与系统覆盖 | CI 仅打 Debian amd64；打包参数接受 arm64 不等于已经有该架构产物。现有核心/systemd 实测主要在 Debian 12 容器，独立 VM/ARM64 和 Debian 13 全流程未验收 | 增加构建矩阵、相应依赖与真机/VM 网络测试 | 构建与验证缺口，可实现 |

关键源码：`deploy/debian/launch.py`、`runtime.sh`、`paopaogw.service`，`sniffbox/crates/sniffbox/src/{production,reload,ovpn_ctl,cf_ctl,geo_cron}.rs`，`integration/{clash_ppsub,clash_policy,subscription_admin}.py`，`FILES/usr/bin/ppg.sh`，`scripts/package-debian.py`。

时区须按功能区分：订阅 subcron 和 restart_cron 使用系统本地时区；流量月清理 net_cleanday 在 Rust 中显式按 UTC+8 计算，不能把后者也描述为当前按 UTC 执行。

## 当前关闭或未充分验收的功能

| 项目 | 当前情况 | 正确结论 |
|---|---|---|
| UDP、QUIC、Full Cone NAT | UDP 代码保留；当前 udp_enable=no，QUIC 还受 route.block_quic 控制 | 不能认定删除，也不能宣称此部署已具备实测的完整 UDP/Full Cone 能力 |
| IPv6 | 代码/配置入口保留；当前 PPGW_IPV6 未开启，缺真实 IPv6 透明入口验收 | 需要主机 IPv6 配置和专项测试 |
| net_rec 流量记录/分布 | 当前 net_rec=false | 页面无记录首先是关闭状态；历史仍在内存，重启不保证保留 |
| DNS burn | 当前运行信息为关闭，代码保留 | 未启用，不是未实现 |
| LAN 1080 显式代理 | 当前 openport=false，ACL/认证代码保留 | 外部未开放，不等于代理功能不存在 |
| SOCKS5 | 模式和认证代码在，缺真实上游端到端验收 | 待验证，不应列作核心重写任务 |
| yaml/suburl | 加载真实订阅、面板和组规则编辑已验证；合成配置执行过真实保存/回滚 | 当前机场通过终端透明入口的完整实际出站、异常场景和长稳性能仍需独立验收 |
| 原生 PPsub 高级能力 | 智能测速、健康检查、SS 入站、前置代理等生成/监督代码保留 | 需要原生 PPsub 模式专项验收，不能与完整 Clash 编辑桥接的限制混为一谈 |

## 不是直接复制上游就能解决的目标

1. **任意 Clash YAML 与原生 PPsub 的完全可逆互转**：二者模型不同，无法保证纯转换不丢语义。可以扩展模型或提供 Clash 原生高级编辑；当前保留原文档、按修改合并的方式应继续保留。
2. **终端→主路由→PaoPaoDNS→网关→远端节点的完整诊断**：网关本机看到的记录不足以证明整条路径。需要 HNM 联合路由器、DNS 和终端的配置、探测或抓包证据。
3. **远端代理实际目标 IP、远端 DNS 选择及完整规则决策证据**：当前 sniffbox 只把流量交给 mihomo/远端代理，不能从一个本机连接快照可靠还原。需要 mihomo 原生观测与关联；发生在远端的过程还需远端证据。
4. **长期完整流量历史**：上游和本 fork 的内存记录都不能替代持久存储。HNM 应另补采集、数据库、保留策略与查询，UDP/嗅探前拒绝等还需新增事件采集。
5. **ISO 开箱即用的主机环境**：改用 Debian 后，网卡、路由、时间同步、内核模块和必要权限须由部署层提供。不是把旧 Dockerfile 改成一个 systemd unit 就能等价获得。

这些不是宣称技术上永远不能实现，而是必须增加适配层、数据模型、观测点或主机配置，不能靠一个开关解决。

## HNM 新增目标与交付缺口

- observer 的身份、能力、状态、FakeIP、有界 TCP 历史和分阶段 HTTP 探测已提供；当前任务未重新核验 HNM 项目自己的 adapter/UI 端到端接入情况。
- yaml/suburl 下的 mihomo 最终规则、节点、链路决策尚不能作为完整证据提供；固定目标 IP、指定节点、强制 IPv4/IPv6、UDP/QUIC 探测仍 unsupported。
- `config_revision` 只覆盖 sniffbox Config，不覆盖 mihomo 订阅、规则及本地策略；需增加 source/policy/effective revision，避免 HNM 把“revision 不变”理解为分流没变。
- 正式交付尚未收口：最近 PPsub 补丁已运行，但旧完整包不包含后续补丁；本地改造仍未提交。应生成统一版本与 manifest，并完成干净 Debian 安装、升级和回退验证。
- CI 未显式安装新增的 `python3-yaml`；安装器检查也未完整覆盖 `nsenter` 等新增需求。此次未执行最新 CI，结论是依赖声明缺口及干净环境失败风险，不是已经确认远程 CI 失败。
- 两个面板端口仍同时存在。面板内 PPsub 已复用登录，但浏览器把 18780/18781 视为不同 origin，跨端口仍可能再次登录；建议收敛到单一管理入口。HTTPS/多用户/RBAC/审计不是当前上游的完整管理功能，应作为新增目标规划。
- 现有 UI 集成仍依赖随库预编译 bundle 的严格位置钩子；上游改版会要求重新核对，打包工具会拒绝无法匹配的 hook。长期应纳入面板源码构建。

## 建议的补齐顺序

1. P0：统一新版安装包、补依赖声明与单一管理入口；完成独立 Debian VM 干净安装和当前订阅真实透明出站验收。
2. P1：订阅/本地策略的自动更新冲突与回退、配置 revision、日志源、时区；把“现在能用”收成可长期维护的部署。
3. P1：按实际需求补 Cloudflared 和 OpenVPN；恢复可证明一致的热重载。
4. P2：PPsub/Clash 高级编辑、IPv6/UDP/Full Cone 专项验收、ARM64 和性能/长稳测试。
5. HNM 并行目标：mihomo 决策/节点关联、终端与 DNS/路由器联合诊断、持久历史存储。

本清单刻意区分代码保留、运行开关、真实验收和新需求；没有依据的功能不计为已支持，也没有把尚未测试的上游核心一律列为未实现。
