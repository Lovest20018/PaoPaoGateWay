# PPsub 当前配置读取补全

> 后续已补齐完整配置的按差异合并保存，撤销只读限制；当前行为和验证见 [完整配置编辑与保存](ppsub-policy-edit-2026-10-27.md)。下文为此前只读阶段记录。

2026-10-27，Asia/Shanghai，已更新 server5 的 `paopaogw-debian-lab`。

此前对完整 Clash/Mihomo 订阅仅构造了一条订阅链接、默认节点组和默认 MATCH 规则，没有读取实际配置。现在从运行中核心的私有命名空间读取 `/tmp/clash.yaml`，展示其中真实的节点组、成员、规则订阅定义及按原顺序排列的规则，不重新下载订阅，也不生成替代策略。

真实页面已验证：29 个节点组、16 个规则订阅定义、17 条原始分流规则；文本模式逐行比对顺序一致，页面无异常。节点密码、控制器 secret 等字段不会进入该 PPsub 投影。

完整 Clash 格式含原生 PPsub 无法等价表达的节点直接引用、策略类型及其他字段。因此这类配置明确以查看模式展示，保存按钮停用；服务端也返回 409，防止旧缓存页面或直接请求将完整配置覆盖为 PPsub 投影。原生 PPsub JSON 仍可正常读取、编辑及保存。

本次只替换 PPsub helper 和 UI 扩展，重启 `paopaogw-subscription`；核心和 HNM API 未重启，核心/API 版本仍为 `0.1.0-hnm-v1.20261027.1`。安装前后 `/etc/paopaogw` 全部文件摘要相同，MainPID 相同。原订阅、规则、当前模式、UUID 和凭据未改变。

验证：Python 35 项通过；浏览器实际读取组数、规则订阅数及全部规则文本通过；服务端阻止 PPsub 覆盖请求返回 409。新增运行依赖为 Debian 的 `python3-yaml`，部署文档、安装器检查和隔离测试镜像已更新。

补丁 `dist/ppsub-current-config-fix.tar.gz` 的 SHA256 为 `a3d968d2da6905af4d7e3485aeee7b40a8f7dbb246cc78aaa76e11bb1d27594d`。服务器副本在 `/opt/paopaogw-lab/`；逐文件 manifest 随包附带。容器内程序/UI 备份为 `/var/lib/paopaogw/admin/before-ppsub-current-config.tar.gz`，验证记录为 `/var/lib/paopaogw/admin/ppsub-current-config-validation.json`。完整配置或凭据未写入本报告。
