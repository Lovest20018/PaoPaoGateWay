# Debian 原生部署

本 fork 主交付是 Debian 12/13 的原生 Linux 二进制、systemd 服务和 Python 标准库 API。ISO 构建脚本保留作上游历史参考，不再参与 fork 的发布流程。首次迁移应使用独立 Debian 实例，完成转发验收后再更改主路由。

## 安装

安装基础依赖：

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-yaml nftables iproute2 procps ca-certificates util-linux openvpn openssl
sha256sum -c paopaogw-版本-debian-amd64.tar.gz.sha256
tar -xzf paopaogw-版本-debian-amd64.tar.gz
cd paopaogw-版本-debian-amd64
sudo python3 install.py
```

安装会校验包内 manifest，不自动启用服务。配置和实例身份首次生成，重复安装保留；更新前须停止核心、API、订阅写入、历史采集与维护服务。源构建请用当前 Rust stable、clang/libclang-dev、cmake、perl、g++，执行 `cargo test --workspace --locked` 和 `cargo build --release --locked -p sniffbox`；不执行 `cargo update`。构建元数据记录 Git 基线提交及源文件摘要，未提交构建不可只用提交号识别。

修改 `/etc/paopaogw/service.env` 的 `PPGW_INTERFACE`，填写实际网卡名，如 `ens18`。IP、默认路由、DNS、NTP 由 Debian 管理。修改 `/etc/paopaogw/files/ppgw.ini`；格式和五种模式沿用上游，密码首次安装随机生成，至少 16 字符。配置不作为 shell 执行。

`free`、`socks5` 不需要 mihomo。`yaml`、`suburl` 模式先运行 `sudo python3 /usr/local/lib/paopaogw/mihomo_update.py install`，直接从 MetaCubeX/mihomo 官方 GitHub 稳定版下载 amd64-compatible 资产，验证官方 SHA256、ELF、版本和现有配置后安装。Geo 数据按规则需要放入 `/var/lib/paopaogw/clash/`，可用原生面板 Geo 更新入口维护。配置文件放 `/etc/paopaogw/files/custom.yaml` 或填订阅地址；依赖远端配置的模式需要目标可达。`ovpn` 的 openvpn/openssl 已列为安装依赖，配置和证书放同一 files 目录；优先使用内嵌证书的单个 .ovpn 文件。Cloudflared 已从 Debian 运行路径、API 和导航入口移除，旧 token 不再启用进程或 nft UID 规则。

```sh
sudo systemctl enable --now paopaogw.service paopaogw-api.service paopaogw-subscription.service paopaogw-observe.service paopaogw-maintenance.timer
sudo systemctl status paopaogw.service paopaogw-api.service
sudo journalctl -u paopaogw.service --since '10 minutes ago'
```

API 默认仅监听 `127.0.0.1:8788`。HNM 连接时，在 `/etc/paopaogw/api.json` 将 `listen` 改成专用管理网地址，并用已有防火墙仅允许 HNM 主机访问该端口；跨不可信网络使用 TLS 反向代理。把 `/etc/paopaogw/read-token` 作为 HNM observer 凭据，保留在服务端，不给 HNM 面板管理凭据。

`probe_urls` 是管理员预配置的精确公开 HTTP(S) URL 列表，测试默认值包含 `https://example.com/`；单次 HEAD、禁用重定向、最多 10 秒、并发 2。配置修改后重启 API 服务。无法强制地址族/固定本次 DNS 地址/指定节点，调用这些选项返回 unsupported。

## 原生 PPsub

使用面板自带的 PPsub 编辑订阅提供商、节点组和规则，沿用面板登录，不再提供独立订阅导入页面。首次启用保存功能需安装 mihomo，并启动内部写入服务：

```sh
sudo systemctl enable --now paopaogw-subscription.service
```

该服务仅监听 `127.0.0.1:8789`。面板经已认证的 `/sniffbox/subscription/config`、`status`、`apply` 固定路由读取和写入，浏览器不需要访问 8789 或再次输入密码。HNM observer Token 不能使用这些管理路由。

原生 PPsub 配置可通过“当前配置”载入，完成编辑后选择“保存并应用”。保存前比较配置版本；重启加载失败且配置未被外部修改时恢复原文件。原有 JSON 导入、导出功能保留。

完整 Clash/Mihomo 配置会从正在运行的核心私有命名空间读取 YAML，并展示实际节点组、规则订阅及按原顺序排列的分流规则。载入后可以直接编辑和保存；服务端根据短期编辑快照识别真正修改的字段，在原 YAML 对象上合并，保留未展示字段、节点凭据和其他顶层设置。重命名组及规则订阅时同步更新引用。不再将完整配置转换成简化 PPsub，也不会生成默认节点组或默认 MATCH 规则。读取只使用本机已加载文件，不重新下载订阅。此功能需要 `python3-yaml` 和 Debian 的 `nsenter`。

保存前在核心相同的文件命名空间内运行 mihomo `-t`；没有修改时不写文件、不重启。修改后按源配置身份保存 `/etc/paopaogw/files/clash-policy.json`，再重启并核对实际加载结果；失败且无外部修改时恢复原配置和原策略。编辑快照最长 30 分钟，运行配置或策略变化后需重新载入。

订阅 URL、更新周期和下载机制继续使用原配置。修改过的节点组、规则及规则订阅区块作为本地策略覆盖，每次订阅下载后重新合并；代理节点及未覆盖字段仍由新订阅更新。替换源订阅后旧策略不会套用到其他来源。完整 Clash 模式支持手动选择、测速、故障转移和智能测速组，关键词映射原生 filter/exclude-filter；前置代理通过独立 provider 的 override.dialer-proxy 应用到节点，保留原 provider 的刷新设置。全局健康监测和定时重启复用原生 PPsub 监督逻辑；Shadowsocks 入站使用单独认证并仅监听 127.0.0.1:8080。高级元数据不会覆盖源订阅的节点凭据。

发布时 `scripts/prepare_dashboard.py` 对随库面板加入最小生命周期和载入钩子，现有 PPsub 仍负责编辑及生成 JSON。上游 bundle 的钩子位置变化会使打包失败，需人工核对；生成资源使用内容摘要文件名，避免旧缓存。

更新时同时停止正在运行的 `paopaogw-subscription`，安装后再启动。该服务不属于 HNM observer API。

## 与宿主隔离

核心通过 systemd mount namespace 使用私有 `/tmp`、`/etc/hosts`、`/www` 和旧式 `/etc/config` 路径，系统真实 hosts 保持不变。不改 Debian 的网络管理器、NTP、IPv6 总开关或整套防火墙。不执行 ISO 脚本的全局清空操作。核心只创建 `ip/ip6 ppgw` 表及 `fwmark 1 → table 100`、priority 11099 的本地策略路由；这些资源已有占用则拒绝启动。规则仅拦截 `PPGW_INTERFACE` 入站。正常停止清理本服务登记的资源，不清空其他表/路由。

现有宿主防火墙仍决定数据是否能到达网关；需要为 FakeIP DNS、管理面板和透明流量按实际网络设置准入。按用户确认，新安装默认开启流量记录、UDP、DNS burn、IPv6、QUIC 支持及诊断白名单；1080 显式代理默认开启，使用 `/etc/paopaogw/proxy-auth` 的随机密码，proxy_cidr 限于回环和私网地址。IPv6 开关和 eth06 标识默认写入；主机 IPv6 地址、路由仍由 Debian 管理，不自动修改全局 IPv6 开关。互斥的 free/socks5/yaml/suburl/ovpn 出站仍须选择一种。升级只在首次应用测试配置时写入标记，以后保留用户修改；可用 `install.py --enable-test-features` 显式重应用。

主路由的 FakeIP/真实地址段静态路由、DNS 转发以及终端实际访问需独立验收。本机代理探测通过不代表透明入口或终端路径通过。

API 独立运行于 `paopaogw-api` 用户。停止 API/HNM 不重启核心；无 `Requires`、`BindsTo` 或自动维护接口。核心停止时 API 返回 `CORE_UNAVAILABLE`，不会尝试修复或重启。核心配置修改可通过控制器原有 reload 路径热更新路由、ACL、认证、嗅探与统计上限等安全字段，保持 boot-id 和活动连接；版本与观测采样受同一配置锁保护。监听、出站模式、记录开关等需 `systemctl restart paopaogw`。持久历史独立存储，内存计数仍会随核心重启清零。不要直接编辑私有 /tmp 文件；持久输入始终位于 files/ppgw.ini。

## 更新、克隆与卸载

更新前备份 `/etc/paopaogw`、`/var/lib/paopaogw` 和原安装包，先停止维护 timer 和所有本项目服务，安装新包后启动。`install.py --root /临时目录` 可在 staging 目录验证安装，不触碰运行服务。新包不替换已有配置、Token、revision key、UUID 和运行数据；mihomo 单独维护。

普通重启保留 instance-id，核心每次启动生成 boot-id。克隆后先停止 API，在克隆机重新生成 `/etc/paopaogw/instance-id` 和 `read-token`，再启动并重新绑定 HNM；不要把克隆作为同一实例。

`sudo sh uninstall.sh` 停止/禁用本项目核心、API、订阅、历史采集和维护服务、移除服务定义与项目程序。配置、身份、凭据和数据保留，便于恢复；固定 UID 用户也保留。其他服务及宿主网络配置不动。

实际已验证的系统、模式、来源摘要及覆盖缺口见 `docs/validation/`。未验收模式不能根据包中存在相关代码就视为通过。


## 官方核心维护与冲突恢复

`paopaogw-maintenance.timer` 启动后每天检查官方稳定版；maintenance.json 的 auto_update 默认 true。可在 PPsub → 网关维护检查、在线更新和回退。下载不经过第三方镜像；官方 digest 缺失、下载校验不符或配置校验失败时不替换。新核心加载失败自动恢复旧二进制，上一成功核心保留为 bin/clash.previous。首次安装核心也可执行上面的 install 命令。该维护权限独立于 HNM observer。

每次订阅刷新都会校验最终配置，成功配置保存于 admin/clash.last-good.yaml。相同来源的无效候选继续使用最后成功版本，维护页面显示冲突；切换来源不会套用旧来源的备份。“恢复订阅原策略”备份本地覆盖后撤销并重新加载，失败时恢复覆盖。runtime API 单独提供源订阅、本地策略和最终配置摘要。

所有服务调度使用 Asia/Shanghai。控制器日志写入 /var/lib/paopaogw/logs/controller.log，保留一个 1 MiB 轮转文件；启动屏页面读取本实例日志。URL 在展示和控制器日志中脱敏。核心输出仍通过 journalctl 查询。

历史采集由 paopaogw-observe 独立运行，默认 30 天、最多 100000 条；HNM 通过 /api/v1/history 查询。mihomo 原生决策快照通过 /api/v1/mihomo/connections 查询。两者都有采样缺口，不能证明完整流量历史或远端 DNS。

升级服务顺序示例（先备份配置、状态和旧包）：

```sh
sudo systemctl stop paopaogw-maintenance.timer
sudo systemctl stop paopaogw-maintenance paopaogw-core-action@check paopaogw-core-action@update paopaogw-core-action@rollback paopaogw-observe paopaogw-subscription paopaogw-api paopaogw
sudo python3 install.py
sudo systemctl enable --now paopaogw paopaogw-api paopaogw-subscription paopaogw-observe paopaogw-maintenance.timer
```
