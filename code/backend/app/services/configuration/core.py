"""配置域共享异常与常量。"""

from __future__ import annotations


class ConfigError(Exception):
    """配置操作失败。message 面向用户（不含任何秘密），可直接回给前端。"""


# 排查只读命令的受支持集合。配置只能在这个集合里开关，不能凭空加入
# 代码不认识、没有参数规则的命令；否则后台改一条配置就可能放行写操作。
READONLY_COMMAND_NAMES = (
    "systemctl", "journalctl", "cat", "head", "tail", "grep", "ls", "stat",
    "du", "df", "ss", "ps", "ip", "free", "uptime", "uname", "hostname",
    "which", "rpm", "dpkg", "dmesg", "top", "vmstat", "iostat", "mpstat",
    "pidstat", "sar", "lsof", "netstat", "ifconfig", "route", "arp", "w",
    "who", "last", "id", "whoami", "date", "wc", "file", "find", "zcat",
    "zgrep", "docker", "kubectl", "apt", "dnf", "nproc", "lscpu", "lsblk",
    "lsmod", "lspci", "lsusb", "sensors", "findmnt", "blkid", "getenforce",
    "sestatus", "modinfo", "pg_isready", "nginx", "apachectl", "httpd",
    "haproxy", "git", "svn", "hg", "npm", "pip", "go", "cargo", "podman",
    "nerdctl", "crictl", "helm", "rabbitmqctl", "rabbitmq-diagnostics",
    "kafka-topics.sh", "mysqladmin", "redis-cli",
)
READONLY_COMMAND_ALLOWLIST_DEFAULT = ",".join(READONLY_COMMAND_NAMES)


# 键白名单：能改的开关就这几个，别的键一律拒 —— 防止随手往键值表里塞垃圾。
# value 一栏是「没配过时的默认值」。默认值不是秘密、跟环境无关，可以写死。
_SETTING_DEFAULTS = {
    "auth_password_enabled": "true",  # 登录方式①：纯账号密码
    "auth_totp_enabled": "true",      # 登录方式②：账号密码 + TOTP
    "auth_sms_enabled": "true",       # 登录方式③：手机号 + 短信
    # 管理员自审批豁免：默认关（职责分离优先）；需要单人闭环时由管理员显式开启
    "self_approval_enabled": "false",
    # 允许排查触达的网段，逗号分隔的 CIDR 列表。
    # 空字符串 = 未配置（后续闸口按「未配置」处理，不会悄悄当「全网放行」）。
    # 强制校验在闸口执行，这里只负责存储和格式校验。
    "allowed_cidrs": "",
    # 大模型「按任务开」总开关，默认关。
    # 两道默认关：每条模型配置 enabled=false + 这个总开关 false。
    # 两道都开、且合规与出网审批通过之前，真实调用一律拒绝并留痕。
    "llm_enabled_by_task": "false",
    # 登录页视觉：管理员在设置里上传/填写的背景图地址；空 = 用内置渐变。
    # 只允许本站 /api/assets/ 下的图片或 http(s) 外链，避免 javascript: 等注入。
    "login_background_url": "",
    # OA 左上角品牌块背景；只影响截图里 OA + 运维 Agent 这一块，
    # 不替换整个左侧导航或业务内容区。
    "brand_background_url": "",
    # 全局字体：只开放 OFL 开源的思源黑体 / 思源宋体，默认思源黑体。
    "ui_font_family": "noto-sans",
    # 全局字号：以 14px 为基准，允许 13 / 14 / 15 / 16 四档。
    "ui_font_size": "14",
    # 部署 AI 自主方案：默认关；开启后模型可以直接产出结构化步骤和命令，
    # 但命令仍要过后端解析、允许/禁止清单、危险档和人工审批。
    "deployment_ai_freeform_enabled": "false",
    # 上传文档 AI 摘要：默认开。关闭后仍生成确定性摘要，不影响上传。
    # 这是「业务开关」，模型是否真的可调用仍受 llm_enabled_by_task + 单模型 enabled
    # + 网关 no_real_call 三道闸口约束；这里关掉只是不尝试调用模型。
    "knowledge_ai_summary_enabled": "true",
    # 知识库 AI 准入：默认开。关闭后规则仍会挡住硬 reject，但“可入库”的
    # 候选转人工复核，不自动 admit。
    "knowledge_ai_curation_enabled": "true",
    # 还没判过的旧自动条目是否先按可入库展示。默认 true，避免升级当天
    # 知识库突然空掉；批量回填完可以关掉，改成“未判定 = review”。
    "knowledge_curation_default_visible": "true",
    # 主机公钥策略：strict 只认已登记；prompt 首次未登记可人工确认；
    # off 仅测试环境，需要额外环境变量确认后才生效。
    "host_key_policy": "prompt",
    # 部署命令允许清单：逗号或换行分隔，按命令前缀匹配；空 = 不允许任何部署命令。
    # 这里是平台配置，不是执行器里的硬编码白名单。
    "deployment_command_allowlist": (
        "apt-get,apt,dnf,yum,rpm,systemctl,service,sed,cp,mv,mkdir,chmod,"
        "chown,tar,unzip,curl,wget,nginx,firewall-cmd,getenforce,setenforce,"
        "hostnamectl,useradd,usermod,groupadd,echo,test,cat,head,tail,grep,ls,"
        "uname,uptime,hostname,whoami,id,date,df,free,ps,ss,netstat,ip,journalctl,"
        "docker ps,docker stats,docker inspect,kubectl get,kubectl describe,kubectl top,"
        # only version query; bare keepalived would start the daemon.
        "keepalived --version,"
        # Windows 只读排障命令：与 worker 白名单保持同一组，避免模板发布时
        # 才报 command_not_allowlisted。
        "Get-ComputerInfo,Get-Service,Get-Process,Get-Date,Get-Volume,"
        "Get-CimInstance,Get-EventLog,Get-WinEvent,Get-Host,Get-Command,"
        "Get-ChildItem,Get-Content,Get-Item,Get-ItemProperty,Get-Location,"
        "Get-NetTCPConnection,Get-NetIPAddress,Get-NetRoute,Get-NetAdapter,"
        "Test-Connection,Test-NetConnection,Test-Path,Resolve-DnsName,"
        "ipconfig,systeminfo,tasklist,ping,tracert,nslookup,route print,arp -a"
    ),
    # 部署命令禁止清单：逗号或换行分隔，按命令前缀匹配；命中即拒绝生成/执行。
    # 默认只是初始策略，管理员可在配置页增删（例如 rm、rm -rf、drop table）。
    "deployment_command_denylist": (
        "rm,rmdir,mkfs,dd,shutdown,reboot,poweroff,halt,init,format,diskpart,"
        "kill,pkill,stop-service,restart-service,remove-item,clear-eventlog,sc delete,"
        # PowerShell 写操作：即使用户手工加进允许清单，默认也先被禁止清单拦下。
        "start-service,set-service,new-service,remove-service,"
        "rename-item,move-item,copy-item,new-item,set-item,set-itemproperty,"
        "new-itemproperty,remove-itemproperty,clear-item,set-content,add-content,"
        "clear-content,out-file,start-process,invoke-expression,invoke-command,"
        "register-scheduledtask,unregister-scheduledtask,set-scheduledtask,"
        "set-executionpolicy,format-volume,repair-volume,resize-partition,"
        "new-partition,remove-partition,restart-computer,stop-computer"
    ),
    # 排查只读命令允许清单：逗号或换行分隔的命令名。
    # 空 = 不允许任何只读排查命令；不是“全部放开”。
    # 配置只能收窄代码已支持的只读命令，参数规则仍在 worker 策略里。
    "investigation_readonly_command_allowlist": READONLY_COMMAND_ALLOWLIST_DEFAULT,
    # 真实只读排查：单独一个开关，不复用运行环境。
    # 生产环境固定真实只读；测试环境由该开关决定是否用样例数据。
    # 排查计划始终 read_only=true，Agent 工具链也只开放只读取证。
    "investigation_real_enabled": "false",
    # Token 预算：0 = 不设预算。单位是 token，不是金额；
    # 金额只按管理员填写的模型单价做估算，单价留空时面板只显示 token。
    "investigation_token_budget_per_task": "0",
    "investigation_token_budget_per_day": "0",
    # 告警自动建单：默认全关。空主机/告警名白名单表示不建单，不是不限。
    "alert_auto_investigation_enabled": "false",
    "alert_auto_max_per_hour": "2",
    "alert_auto_max_per_day": "6",
    "alert_auto_max_concurrent": "2",
    "alert_auto_min_interval_minutes": "60",
    "alert_auto_min_firing_seconds": "120",
    "alert_auto_allow_severities": "critical",
    "alert_auto_allow_hosts": "",
    "alert_auto_allow_alertnames": "",
    "alert_auto_flap_window_minutes": "30",
    "alert_auto_flap_transitions": "2",
    "alert_auto_flap_cooldown_minutes": "60",
    "alert_auto_storm_window_minutes": "5",
    "alert_auto_storm_threshold": "3",
    "alert_auto_storm_cooldown_minutes": "30",
}

# 布尔型开关：值只能是 true / false
_BOOLEAN_KEYS = {
    "auth_password_enabled", "auth_totp_enabled",
    "auth_sms_enabled", "self_approval_enabled",
    "llm_enabled_by_task", "deployment_ai_freeform_enabled",
    "knowledge_ai_summary_enabled",
    "knowledge_ai_curation_enabled",
    "knowledge_curation_default_visible",
    "investigation_real_enabled",
    "alert_auto_investigation_enabled",
}

# 告警自动建单的数值/清单开关；空清单表示不建单，不是“不限”。
# 具体范围和清单校验在 settings.py 里处理。


_SETTING_NAMESPACE = "setting"
_COMMAND_LIST_KEYS = {
    "deployment_command_allowlist", "deployment_command_denylist",
}
_COMMAND_LIST_META = (";", "|", "&", ">", "<", "`", "$(")


# 类型白名单。含第 ⑩ 步蓝鲸两套接法（A 直连 / B 自包 MCP）和第 ⑧ 步语料
# 三件套（obsidian / feishu / siyuan）—— 开工清单要求「都留接口」。
NETWORK_KINDS = {
    "prometheus", "elk", "k8s", "docker",
    "blueking_a", "blueking_b", "feishu", "siyuan",
}
PATH_KINDS = {"obsidian"}   # Obsidian 是本地目录路径，不是 URL
ALL_KINDS = NETWORK_KINDS | PATH_KINDS
ADAPTERS = {"self", "mcp"}  # self = 自研适配器；mcp = 走插件层（第⑨步）


_DS_NAMESPACE = "datasource"

# 一次查询要带的列：key 是身份，value 里放业务字段，另外两列是配置属性
_DS_SELECT = "SELECT key, value, credential_ref, enabled, updated_at FROM v3_configs"
