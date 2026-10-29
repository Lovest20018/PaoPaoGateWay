# HNM Integration API v1

依据 HNM `paopaogw-api-requirements-v1.md`（2026-10-26）实现 provider，当前契约修订 1.2。HTTP 契约为 [openapi-v1.json](openapi-v1.json)，确定性样例位于 `integration/fixtures/`。HNM adapter/Agent/页面由 HNM 仓库实现，本仓库交付 provider。

所有路径使用 `Authorization: Bearer <read-token>`。Token 不在 URL、日志、错误正文里使用。`X-Request-ID` 接收 8–64 个 ASCII 字母/数字/下划线/短横线；无效/缺少时生成 UUID，所有业务响应都返回对应 header 和 `request_id`。`correlation_id` 单独用于探测关联（1–64 字符），不充当授权。

成功响应：`api_version="1"`、`instance_id`、`request_id`、`fetched_at`、`sampled_at`、`data`。时间均是 UTC Unix 秒浮点数；连接 `start` 沿用 RFC3339。`fetched_at` 是 API 获取时间，`sampled_at` 是核心原始采样时间；不可取得则 null。freshness 最大年龄 25 秒，未来超过 5 秒/缺少时间为 stale；HTTP 200 不等于转发或探测成功。错误为同一外层加 `error.code`，不返回原始上游异常。

| 端点 | 首批语义 |
|---|---|
| GET instance | 持久 UUID、发行版本、Git 基线、核心版本、核心 boot-id、有效配置 revision |
| GET capabilities | observer 权限、当前运行模式、功能状态、精确探测白名单及上限；核心不可用返回 503 |
| GET runtime / health | 核心响应、配置加载状态/revision、FakeIP CIDR、可信 DNS、原始采样/过期状态；出站可达 unknown，转发验证 false |
| GET metrics | 流量 counter、连接数/内存 gauge，单位、原始采样时刻、boot-id；counter 重启清零，0 保留，缺测 null/available=false |
| GET connections | 默认 snapshot 保留首批格式；view=recent / observed_active 使用核心直接观测，支持 domain/client/since/until 过滤，recent 支持 cursor |
| POST diagnostics/probe | 精确白名单 URL、当前出站、HEAD、不跟随重定向；timeout_ms 1000–10000，correlation_id 可选 |
| GET fakeip/lookup | domain / ip 二选一，实际 FakeIP 缓存查询；matched/no_records，不分配地址、不触发 DNS、不改变 LRU |
| GET decisions | 默认 recent，返回 TCP 实际模式派发及建连结果；mihomo 内部规则/最终节点 unavailable |
| GET mihomo/connections | yaml/suburl 下直接读取 mihomo 当前快照，返回实际 rule/rulePayload/chains；limit 1–500，不猜远端 DNS 或最终目标 |
| GET history | SQLite 持久观测；limit 1–500，before 为上一页 next_before，domain/client/source 精确筛选；source 为 sniffbox_lifecycle 或 mihomo_snapshot |

旧 snapshot 的 `metadata.sourceIP` 是网关实际看见的地址，不能穿透上游 NAT 识别终端。`destinationIP` 是入口原目的地址。旧上游缓存没有采样时间，因此保持 `sampled_at=null`、`freshness_known=false`；该 view 的时间过滤仍为 unsupported。

新观测 view 的地址/时间为结构化字段：started_at、ended_at、last_observed_at 均是 UTC Unix 秒。original_destination 可以是 FakeIP，actual_target_address 仅在 free/ovpn 的真实 socket peer 可观测时返回；SOCKS/mihomo 的 upstream_peer 只是代理地址，不冒充最终目标。resolution.source 区分 literal_destination、gateway_resolver_or_cache、remote_proxy_unknown；selected_address 是该次实际连接选择的地址，不是完整 DNS 回答。NAT 后真实终端身份保持未知。

TCP 记录由转发 begin/connect/finish 钩子收集；scope=tcp_after_sniff，未覆盖嗅探前拒绝、UDP、被阻断流量和核心异常退出。recent 在核心内最多保留 1024 条、最长 300 秒，完成后记录不可变；active 观测也有 1024 条容量。discarded_records 统计容量/时间淘汰和无法跟踪的记录，complete_history 始终 false。last_observed_at 对活动项是最近已记录事件时间，响应 sampled_at 是读取时间。短连接完成即进入 recent，无需轮询捕捉。

recent 按核心单调 sequence 升序，cursor 绑定 boot-id、端点、归一化查询条件并签名；可改变 limit，不能与 offset 混用。普通核心重启返回 409 CURSOR_EXPIRED；筛选变化/篡改返回 400 INVALID_CURSOR。next_cursor 供继续读取，gap=true 表示游标之后有已淘汰数据。offset 仅用于有界即时翻页，不承诺稳定快照。无记录只表示未观察到。

decision.rule_kind=mode_dispatch，rule_id=mode:free/socks5/ovpn 等表示实际出站模式派发，不是按域名规则命中。free 明确 free_mode_no_rule；固定 SOCKS/OpenVPN 返回非敏感出站 ID，connect_result 区分 unknown/connected/failed，应用层结果始终 unknown。mihomo 返回 unavailable，不能根据模式或配置猜节点；探测不会改全局节点。

FakeIP lookup 的 source=actual_fakeip_cache，只报告当前实际分配的对应关系，哈希碰撞按核心真实分配结果处理。dns_ttl_seconds 是 DNS 回答 TTL，缓存本身按 LRU 淘汰，没有对应硬到期，valid_until=null、validity=until_evicted_or_core_restart。不能用当前映射反推历史所有者，historical_ownership_verified=false。

探测 source 固定为 gateway_local_proxy，通过核心已有 loopback 1079 健康探测入口，HEAD、不重定向、不读取正文，HTTP 头最多 32 KiB。TLS 使用系统 CA 和目标域名 SNI，证书校验开启。总预算内分别报告 local_proxy/TLS/HTTP 耗时；TLS 阶段包含等待核心 DNS/TCP 的时间，不能把它当纯 TLS 握手时间。SOCKS 提前接受本地连接本身不证明目标 TCP 已建连。

probe 使用相同 loopback socket 源端口、目标域名和 boot-id 与核心记录精确关联，返回 connection_id、可取得的实际地址/出站 ID；只按时间相近不关联。TCP 成功可由核心建连或实际 TLS/HTTP 完成证明；连接失败定位 outbound_connect，但该聚合阶段可能包含解析/隧道等，未进一步测量时不猜具体子阶段。TLS/HTTP 错误按实际失败的阶段报告。HTTP 2xx/3xx 为 passed（3xx 未跟随），4xx/5xx 为 failed。请求前后 boot/revision 改变为 unknown。完整 DNS RRset、远端代理最终地址及 mihomo 节点仍可为 unknown/null。

旧核心缺少 evidence_schema=1 时禁用新查询，保留已有观测和阶段探测；不能伪造新能力。强制地址族、固定地址、指定节点仍为 unsupported。runtime.last_probe 保留 API 进程内最近一次结果及原始时刻，超过 25 秒标 stale，核心重启/配置变化不复用；它不会把全局 reachability 改成 true，也不能证明透明转发或终端路径成功。

配置 revision 是以本机持久秘密为 key 对核心实际加载配置做摘要，避免公开凭据的字典校验值；revision_scope=sniffbox_config 不覆盖 mihomo 动态选点/规则、外部订阅或 nft 状态，不可当整套网关配置版本。每条模式派发记录绑定其开始时的核心 revision。API 不能读取完整配置、订阅 URL、认证值或维护核心。

runtime.mihomo_configuration 单独报告 source_revision、policy_revision、effective_revision 和 loaded/conflict 状态，均为持久密钥保护的摘要。自动刷新候选未通过校验时保留最后成功配置及其摘要，status=conflict；这些摘要不覆盖动态选点。核心安全字段热加载保持 boot-id，revision 随实际加载值更新；更换模式或监听参数要求重启。

持久历史由独立 paopaogw-observe 服务每秒采集，保留 30 天、最多 100000 条，可在 maintenance.json 调整天数或关闭采集。按 boot/id/source 去重并更新最后观测值；first_seen_at/last_seen_at 是采集时间，record 中的事件时间沿用来源。核心和 API 重启不会清空数据库，实例数据目录应按凭据同等保护。列表按插入 rowid 倒序，before 是不透明的数值分页位置，记录更新不改变其位置。轮询会漏掉短暂 mihomo 连接；即使有持久历史，complete_history 仍为 false。原生 TCP 缓冲有容量和时间缺口，未关联的 sniffbox 与 mihomo 记录不按域名/时间强行拼接。

上限：HTTP 请求体 4 KiB，核心响应 4 MiB，对外响应 512 KiB，HTTP 工作线程 8，主动探测并发 2。超过列表上限返回 400；过大核心响应返回 502。主动探测默认关闭；API 不自动探测、不缓存健康成功、不自动重启核心。

稳定错误码：401 UNAUTHORIZED；400 INVALID_* / UNEXPECTED_BODY / CURSOR_OFFSET_CONFLICT；403 TARGET_NOT_ALLOWED；404 NOT_FOUND；405 METHOD_NOT_ALLOWED；409 CURSOR_EXPIRED / CORE_CHANGED；413 BODY_TOO_LARGE；429 PROBE_BUSY；501 UNSUPPORTED / TIME_FILTER_UNSUPPORTED；502 INCOMPATIBLE_CORE / INVALID_CORE_RESPONSE / CORE_RESPONSE_TOO_LARGE / RESPONSE_TOO_LARGE；503 CORE_UNAVAILABLE / STALE_DATA / UNAVAILABLE。未知能力不要按端点存在推断支持。

| 当前模式 | runtime/metrics | 活动快照 | 当前代理出站探测 | FakeIP/分流决定 |
|---|---|---|---|---|
| free / socks5 | 支持 | 快照 + 有界 TCP 缓冲 | 白名单开启后支持 | 实际缓存 / 模式派发与建连结果 |
| yaml / suburl | 支持，需 mihomo | 缓存快照 + 网关侧 TCP 缓冲 | 白名单开启后支持 | 实际缓存 / mihomo 规则节点 unavailable |
| ovpn | 支持，需 OpenVPN | 快照 + 有界 TCP 缓冲 | 白名单开启后支持 | 实际缓存 / 模式派发与建连结果 |

此表是代码能力；实际验收矩阵记录在独立 validation 文档，不把未测试模式列为已验收。API 不依赖网页 DOM，HNM 只依赖 v1 契约。
