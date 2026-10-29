# 完整 Clash 配置在原生 PPsub 中编辑与保存

2026-10-27，Asia/Shanghai，已更新 server5 的 `paopaogw-debian-lab`。覆盖此前“Clash 投影只读”的临时限制。

入口仍为 `http://192.168.1.41:18781/ui/#/ppsub` 或原 18780 面板；点击“当前配置”后编辑并保存，沿用一次面板登录。实际读取 29 个组、17 条规则、16 个规则订阅及 45 个内嵌节点。节点来源选择器现在也能选取内嵌节点。

服务端保留完整原始 YAML 的短期快照，编辑器通过稳定对象标识提交修改。合并只更新变化的组成员、组模式/测速参数、过滤条件、规则、规则订阅和已有订阅链接；未展示字段原样保留。组名和规则订阅名称修改会更新引用。规则导出在完整配置模式下保留原始规则行，包含上游 PPsub 未列出的类型和 `#` 字符。

快照最长 30 分钟，并绑定 INI、已加载文档与现有策略的摘要。配置被外部修改、订阅自动刷新或快照失效时拒绝陈旧保存。没有修改返回 `unchanged`，不落盘、不重启。

修改在原配置上生成候选，先用实际 mihomo `-t` 校验，再保存有来源绑定的 `clash-policy.json`。订阅更新后只覆盖自定义的策略区块；节点和其他未覆盖字段继续更新。加载失败且没有外部修改时恢复 INI 与旧策略。HNM observer 无管理权限，API 契约与 Token 不变。

完整配置模式目前不转换 PPsub 专有全局健康监测、定时重启、Shadowsocks 入站或智能测速；修改这些选项会明确返回错误。新增/删除完整源订阅条目及其更名不在桥接范围，已有链接可编辑；原生 PPsub JSON 工作流仍保留。

验证：

- Python 42 项通过；浏览器真实 PPsub 导出对象往返保留隐藏字段、fallback 类型与间隔、重复成员、未知规则类型以及包含 `#` 的载荷。
- 独立临时 Debian/systemd 实例使用合成节点完成真实“组重命名及规则编辑→mihomo 校验→保存→核心重启→实际加载核对”。其他节点凭据、DNS、secret、隐藏字段和当前源模式保留。
- 无效节点引用在写入前被拒绝；无改动保存不重启；源节点端口变更并重启后，本地组/规则策略仍保留且新节点信息生效。
- 仅在独立验证实例注入一次 systemctl restart 失败，旧策略和原配置恢复成功；该临时实例已清理，结果存于 server5 `/opt/paopaogw-lab/ppsub-policy-isolated-validation.json`。
- 用户真实订阅只执行无改动保存：返回 unchanged；配置目录全部文件摘要和核心 MainPID 相同，未创建策略文件，页面无异常。结果在容器 `/var/lib/paopaogw/admin/ppsub-policy-live-validation.json`。

补丁为 `dist/ppsub-policy-fix.tar.gz`，SHA256：`fd17cb6dd33c7d58ad1747039e9530e4aec424a4ff5e6605cc21a3b2a33f791c`，随附逐文件 manifest。此次更新 Python helper、面板扩展及 Debian ppg.sh 合并挂钩，核心/API 仍为 `0.1.0-hnm-v1.20261027.1`；旧完整包不包含此后补丁。容器内更新前备份为 `/var/lib/paopaogw/admin/before-ppsub-policy-edit.tar.gz`。
