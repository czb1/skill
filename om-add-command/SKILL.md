---
name: om-add-command
description: OM工具自动化技能，自动化创建任意MML命令（SET/LST/ADD/RMV/MOD等）。支持自定义每个命令的输入/输出参数，通过配置params数组控制参数类型（input/output/input&output）和是否必选。
---

# UPCF Add Command Skill

## 概述

自动化调用OM工具API，完成任意MML命令的完整开发流程：
- 校验omres-cli登录态（登录已在上游统一完成，本skill不登录）、创建私人工程
- 上传解析建模文件、创建原子对象
- 创建自定义枚举类型、添加字段
- 设置字段数据类型
- 创建MML命令、添加命令参数
- 校验、导出模型

## 核心特性

1. **支持任意命令类型**：SET/LST/ADD/RMV/MOD等
2. **自定义参数配置**：通过params数组定义每个命令的输入/输出参数
3. **动态方法管理**：根据commands配置自动删除不需要的方法
4. **多枚举类型支持**：为每个枚举字段自动创建独立的枚举类型，枚举项从range字符串自动解析（如"SUBID（0）APN （1）"）
5. **自动导出与MR创建**：校验通过后自动导出模型、解压压缩包、同步文件到仓库、提交Git并通过 `codehub-cli` 创建MR（复用统一登录，无需token）
6. **错误码与Lua脚本**：支持添加错误码和生成业务处理Lua脚本

## 前置条件：omres-cli 登录态

**omres-cli 的登录已在上游统一完成（E2E 流程的阶段零 Step 1，由用户在自己的终端亲自执行），本 skill 不登录、也不接受任何凭据。**

- 登录态保存在 `%USERPROFILE%\.omres-cli\session.json`（权限0600），对同一用户下的所有进程可见，子代理直接调用 `omres-cli` 即可。
- 工作流第一步执行 `omres-cli auth status` 校验登录态，按退出码分支：

  | 退出码 | 含义 | skill行为 |
  |--------|------|-----------|
  | 0 | 已认证 | 继续执行后续步骤 |
  | 3 | 未认证 / 会话已过期 | **立即失败返回**，提示主代理让用户重新执行 `omres-cli auth login`，用户确认后从本阶段重跑 |
  | 1 | 其它错误（后端不可达等） | 立即失败返回，说明不是认证问题，不要引导重新登录 |

- **严禁**在本 skill 中执行 `omres-cli auth login`，严禁索要、读取、传递或落盘任何账号密码。
- `execute_workflow` 不再需要 `passwd` 参数；`userName` / `w3Num` 也可以省略，默认从登录态中解析。为兼容旧调用，传入 `passwd` 不会报错，但会被忽略并打印警告。

### codehub-cli 登录态（创建MR用）

校验通过后的「提交Git并创建MR」同样复用统一登录：MR 通过 `codehub-cli mr create` 创建，鉴权来自阶段零完成的 `codehub-cli auth login`，**skill 不再读取任何 CodeHub token**（原先从本地 `config.json` 读 `codehub_token` 的方式已废弃，也不使用 codehub-cli 的 `-t/--token` 与 `CODEHUB_TOKEN`）。

实际执行的命令形如（在代码仓目录下执行，便于 codehub-cli 自身的仓库探测生效）：

```bash
codehub-cli mr create -p UPCF/ComConfig -H https://szv-y.codehub.huawei.com \
  --source-branch <本次分支> --target-branch master \
  --title "[WIP] feat: 添加xxx" --description "..." -f json
```

- **项目与Host自动推断**：取值顺序为 显式参数 → `CODEHUB_PROJECT` / `CODEHUB_HOST` 环境变量 → 代码仓 `git remote get-url origin` 推断（如 `https://szv-y.codehub.huawei.com/UPCF/ComConfig.git` → project `UPCF/ComConfig`、host `https://szv-y.codehub.huawei.com`）→ 项目兜底 `UPCF/ComConfig`、Host 交给 codehub-cli 自身配置。
- 输出用 `-f json`，解析失败时再从文本里正则提取 `.../merge_requests/<iid>`，返回 `{'mr_url', 'mr_iid'}`。
- codehub-cli 不在 PATH 时可用 `CODEHUB_CLI` 环境变量指定可执行文件全路径。
- 若 codehub-cli 报未认证，同样是**直接失败返回**，由主代理提示用户执行 `codehub-cli auth login` 后重跑，不代为登录。

### omres-cli 可执行文件的定位

所有脚本统一走 `omres_cli.find_omres_cli()`，查找顺序：`OMRES_CLI` 环境变量 → PATH → 阶段零安装目录 `~/omres-cli/` → 裸命令名。

> 阶段零用 `setx` 写入的 PATH 对**已启动**的进程不生效，所以保留了对安装目录的回落；如果调用方所在进程 PATH 里没有 omres-cli，用 `OMRES_CLI` 指定全路径即可。

## 工作流程

### 主入口

```python
from workflow import execute_workflow

result = execute_workflow(
    taskName="ZL0605_STATOBJ",
    serviceName="PcfPolicyEngineService",  # 使用服务名称，moduleId和moduleName自动映射
    moc_name="STATSESOBJ",
    moc_desc_ch="话统统计对象",
    moc_desc_en="Performance Measurement Object",
    enum_type_name="SubIdMatchType",
    fields=[
        {"name": "INDEX", "type": "INT32", "isKey": 1, "range": "1~128"},
        {"name": "OBJID", "type": "ENUM", "isKey": 0, "range": "SUBID（0）APN （1）"},
        {"name": "MATCHTYPE", "type": "ENUM", "isKey": 0, "range": "PREFIX （0）POSTFIX （1）"},
        {"name": "OBJPARA", "type": "STRING", "isKey": 0, "range": "5~15"}
    ],
    commands=[
        {
            "name": "ADD STATSESOBJ",
            "type": "create",
            "params": [
                {"field": "INDEX", "io": "input", "required": True},
                {"field": "OBJID", "io": "input", "required": True},
                {"field": "MATCHTYPE", "io": "input", "required": True},
                {"field": "OBJPARA", "io": "input", "required": True}
            ]
        },
        {
            "name": "RMV STATSESOBJ",
            "type": "delete",
            "params": [
                {"field": "INDEX", "io": "input", "required": True}
            ]
        },
        {
            "name": "LST STATSESOBJ",
            "type": "get-config",
            "params": [
                {"field": "INDEX", "io": "input&output", "required": False},
                {"field": "OBJID", "io": "input&output", "required": False},
                {"field": "MATCHTYPE", "io": "output", "required": False},
                {"field": "OBJPARA", "io": "output", "required": False}
            ]
        }
    ],
    file_path="D:/git/26.0/ComConfig/om",  # 只需指定目录，会自动压缩
    base_url="https://omtool.rnd.huawei.com"
)
```

## file_path参数说明
- 如果file_path是ComConfig目录，需要把file_path修改到ComConfig/om，上面实例中的"D:/git/26.0/ComConfig/om"只是例子，实际路径需要你自己动态调整，请根据代码仓真实路径调整
- **指定目录路径**：如 `D:/path/to/service_dirs`，会自动将目录下所有服务文件夹压缩为zip后上传
- 生成的压缩包自动命名为 `ZL0605_{timestamp}.zip`

## serviceName参数说明

execute_workflow函数使用serviceName参数代替原来的moduleId和moduleName参数：
- **serviceName**: 服务名称（如PcfPolicyEngineService），根据SERVICE_MAP自动获取对应的moduleId和moduleName
- **moduleId**: 自动从SERVICE_MAP获取，使用json中的id字段值
- **moduleName**: 自动从SERVICE_MAP获取，用于API调用

调用create_mml_command时，service参数会使用完整的serviceName（如PcfPolicyEngineService），moduleId使用SERVICE_MAP中的id值。

## commands配置说明

每个命令配置包含以下字段：

| 字段 | 说明 |
|------|------|
| name | 命令名称，如"ADD STATSESOBJ" |
| type | 命令类型：create/add, delete/rmv, update/set/mod, get-config/lst |
| params | 参数配置数组 |
| branches | 分支条件配置数组（可选），用于配置条件显示的参数 |

### params参数配置

| 字段 | 说明 |
|------|------|
| field | 字段名称，对应fields中定义的字段 |
| io | 参数类型：input(输入参数), output(输出参数), input&output(输入&输出) |
| required | 是否必选：True/False |

### branches分支条件配置

当某个参数的显示依赖于其他参数的值时，使用branches配置条件显示。

| 字段 | 说明 |
|------|------|
| switchField | 触发条件字段名称（如ALARMSW） |
| triggerValue | 触发值（如1表示ON） |
| childFields | 条件满足时显示的字段列表 |

**示例**：当ALARMSW为ON时显示HIGHTSTR、HIGHTEND、OVERLOADSTR、OVERLOADEND

```python
commands=[
    {
        "name": "SET DLBSCTPBUFFCFG",
        "type": "update",
        "params": [
            {"field": "BUFFTYPE", "io": "input", "required": True},
            {"field": "ALARMSW", "io": "input", "required": False},
            {"field": "HIGHTSTR", "io": "input", "required": False},
            {"field": "HIGHTEND", "io": "input", "required": False},
            {"field": "OVERLOADSTR", "io": "input", "required": False},
            {"field": "OVERLOADEND", "io": "input", "required": False}
        ],
        "branches": [
            {
                "switchField": "ALARMSW",
                "triggerValue": 1,  # ON对应的枚举值
                "childFields": ["HIGHTSTR", "HIGHTEND", "OVERLOADSTR", "OVERLOADEND"]
            }
        ]
    }
]
```

### type到commandType的映射

| type值 | commandType值 |
|--------|---------------|
| create | Add |
| delete | Remove |
| update | Modify |
| get-config | Lst |

### 动态方法删除规则

- 用户输入ADD/RMV/LST时 → 删除update方法，保留create/delete/get-config
- 用户输入ADD/MOD/RMV/LST时 → 保留所有方法
- 用户输入SET/LST时 → 删除create/delete方法，保留update/get-config

## 服务映射参考

workflow.py通过`fetch_service_map()`在创建工程后调用 `omres-cli overallview search` CLI命令动态获取SERVICE_MAP，根据serviceName自动获取moduleId和moduleName。以下为CLI返回的典型映射参考（实际值以CLI返回为准）：

| id (moduleId) | moduleName | serviceName |
|---------------|------------|-------------|
| 1 | PCFLCS | PcfLicenseCenterService |
| 2 | PCFSGM | PcfSgmService |
| 3 | PCFDCS | PcfDisCtrlService |
| 4 | PCFIPS | PcfIntelligentPolicyService |
| 5 | PCFHLB | PcfHttpLoadBalanceService |
| 6 | PCFNCS | PcfNrfClientService |
| 7 | PCFDRS | PcfDataReportService |
| 8 | PCFOTCS | PcfOfflineTaskCtrlService |
| 9 | PCFPMS | PcfPolicyManagementService |
| 10 | PCFNLF | PcfNotifyLogFileService |
| 11 | PCFTPES | PcfTwinPolicyEngineService |
| 12 | PCFPES | PcfPolicyEngineService |
| 13 | PCFNTS | PcfNotificationTaskService |
| 14 | PCFDLB | PcfDiamLoadBalanceService |
| 15 | PCFAPPCTRL | PcfAppCtrlService |

**注意**：SERVICE_MAP在代码中初始为空，由`fetch_service_map()`动态填充。moduleId使用API返回的id字段值，service使用完整的serviceName。若API获取失败，默认fallback为moduleId=11（PCFTPES）。

## 数据类型ID参考

| 类型 | dataTypeId | 说明 |
|------|------------|------|
| INT8 | 11 | 8位整数 |
| INT16 | 12 | 16位整数 |
| INT32 | 13 | 32位整数 |
| INT64 | 14 | 64位整数 |
| UINT8 | 15 | 无符号8位整数 |
| UINT16 | 16 | 无符号16位整数 |
| UINT32 | 17 | 无符号32位整数 |
| UINT64 | 18 | 无符号64位整数 |
| STRING | 19 | 字符串 |
| BOOL | 21 | 布尔 |
| ENUM | 25 | 枚举 |
| IPV4 | 29 | IPv4地址 |
| IPV6 | 30 | IPv6地址 |

## 字段取值范围格式说明

### 整型字段 (INT8/INT16/INT32/INT64/UINT8/UINT16/UINT32/UINT64)

取值范围格式使用 `~` 连接符，例如 `0~100`、`1~128`。

**注意**：必须使用 `~` 而不是 `-`，使用 `-` 会导致校验失败。

### 枚举字段 (ENUM)

取值范围格式为 `枚举项名(值)`，多个枚举项之间用逗号分隔，例如：
- `SENDBUFF(0),RECVBUFF(1)`
- `ON(0),OFF(1)`
- `SUBID(0),APN(1)`

### 字符串字段 (STRING)

取值范围格式为 `最小长度~最大长度`，例如 `5~15`。

可通过 `maxLength` 属性指定最大长度限制：
```python
{"name": "OBJPARA", "type": "STRING", "isKey": 0, "range": "5~15", "maxLength": 15}
```

## 登录态说明

- 不需要在本 skill 中配置或读取任何凭据：`.e2e_files/env_info.json` 里的密码、Windows凭据管理器（keyring）都不再使用。
- 登录态由 `omres-cli` 自行维护，后端未下发过期时间时按默认8小时判定（可用 `OMRES_SESSION_TTL_HOURS` 覆盖）。
- 如需确认会话在后端真实有效（而不只是本地存在），可用 `omres-cli auth status --online` 额外探活；`ensure_authenticated(context, online=True)` 对应该行为。
- 会话中途失效时，工作流会在第一步就以「未认证」失败返回，由主代理提示用户重新登录后**从本阶段重跑**，skill 不得自行重登。

### base_url 与登录 server 的关系

- session.json 里的 cookie 是**绑定到登录时那台 server** 的。只要本地存在登录态，工作流一律以登录态里的 server 为准：不再给 omres-cli 传 `--server`，同时把 `context.base_url` 自动对齐过去。`base_url` 参数只在本地**没有**登录态时才生效。
- 两者不一致时（例如登录的是 `http://10.243.80.228`，而调用传了 `https://omtool.rnd.huawei.com`）会打印一条告警，说明已改用登录态的 server。确需访问另一个地址，请先对该地址执行 `omres-cli auth login`。
- 这是一个容易误诊的故障：地址被覆盖后 cookie 带不过去，后端返回 `noLogin`，但 `omres-cli auth status` 只看本地会话，仍然显示「已认证」，看起来像会话过期。判断依据是**不带 `--server` 直接跑 omres-cli 能成功**。
- 极少数需要保留旧行为（强行用 `base_url` 覆盖 server）的场景，设置环境变量 `OMRES_ALLOW_SERVER_OVERRIDE=1`。

## 执行方式

在PowerShell中执行（Windows环境）：

注意下面的path/to/skill要你自己去获取本skill（目录名 `om-add-command`，在E2E流程中以 `upcf-add-command` 的名义被委派）的实际路径。
```powershell
cd "path/to/skill/scripts"; python -c "
import sys
sys.stdout = open('workflow_output.log', 'w', encoding='utf-8')
sys.stderr = sys.stdout
from workflow import execute_workflow
from datetime import datetime
import random

ts = datetime.now().strftime('%H%M%S')[2:] + str(random.randint(10, 99))

result = execute_workflow(
    taskName=f'ZL0605_DLBSCTPBUFFCFG{ts}',
    serviceName='PcfDiamLoadBalanceService',
    moc_name='DLBSCTPBUFFCFG',
    moc_desc_ch='DLB SCTP缓冲区告警配置管理',
    moc_desc_en='DLB SCTP Buffer Alarm Configuration Management',
    enum_type_name='DlbSctpBuffCfg',
    fields=[
        {'name': 'BUFFTYPE', 'type': 'ENUM', 'isKey': 1, 'range': 'SENDBUFF（0）,RECVBUFF（1）'},
        {'name': 'ALARMSW', 'type': 'ENUM', 'isKey': 0, 'range': 'ON（0）,OFF（1）'},
        {'name': 'HIGHTSTR', 'type': 'UINT32', 'isKey': 0, 'range': '0~100'},
        {'name': 'HIGHTEND', 'type': 'UINT32', 'isKey': 0, 'range': '0~100'},
        {'name': 'OVERLOADSTR', 'type': 'UINT32', 'isKey': 0, 'range': '0~100'},
        {'name': 'OVERLOADEND', 'type': 'UINT32', 'isKey': 0, 'range': '0~100'}
    ],
    commands=[
        {
            'name': 'SET DLBSCTPBUFFCFG',
            'type': 'update',
            'params': [
                {'field': 'BUFFTYPE', 'io': 'input', 'required': True},
                {'field': 'ALARMSW', 'io': 'input', 'required': True},
                {'field': 'HIGHTSTR', 'io': 'input', 'required': True},
                {'field': 'HIGHTEND', 'io': 'input', 'required': True},
                {'field': 'OVERLOADSTR', 'io': 'input', 'required': True},
                {'field': 'OVERLOADEND', 'io': 'input', 'required': True}
            ],
            'branches': [
                {
                    'switchField': 'ALARMSW',
                    'triggerValue': 0,
                    'childFields': ['HIGHTSTR', 'HIGHTEND', 'OVERLOADSTR', 'OVERLOADEND']
                }
            ],
            'defaultRecordsList': [
                {'BUFFTYPE': 'SENDBUFF', 'ALARMSW': 'ON', 'HIGHTSTR': '50', 'HIGHTEND': '40', 'OVERLOADSTR': '70', 'OVERLOADEND': '60'},
                {'BUFFTYPE': 'RECVBUFF', 'ALARMSW': 'ON', 'HIGHTSTR': '50', 'HIGHTEND': '40', 'OVERLOADSTR': '70', 'OVERLOADEND': '60'}
            ]
        },
        {
            'name': 'LST DLBSCTPBUFFCFG',
            'type': 'get-config',
            'params': [
                {'field': 'BUFFTYPE', 'io': 'input&output', 'required': False},
                {'field': 'ALARMSW', 'io': 'output', 'required': False},
                {'field': 'HIGHTSTR', 'io': 'output', 'required': False},
                {'field': 'HIGHTEND', 'io': 'output', 'required': False},
                {'field': 'OVERLOADSTR', 'io': 'output', 'required': False},
                {'field': 'OVERLOADEND', 'io': 'output', 'required': False}
            ]
        }
    ],
    file_path='D:/git/26.0/ComConfig/om',
    base_url='https://omtool.rnd.huawei.com',
    error_codes=[
        {
            'code': 'ZL_58362',
            'code_num': 58362,
            'descCh': '缓冲区阈值冲突',
            'descEn': 'Buffer threshold conflict.'
        }
    ]
)
print()
print('最终结果:')
print(result)
" 2>&1
```

**注意**：
- 执行前无需任何登录动作；若 `omres-cli auth status` 返回未认证，脚本会在第一步立即失败并返回提示，此时直接把失败原因回报主代理，**不要尝试登录或索要密码**
- 工作流程涉及多次HTTP API调用，预计需要3-5分钟
- 执行时超时时间设置为**20分钟（1200000ms）**
- 日志输出重定向到`workflow_output.log`避免截断

## 返回结果

```python
{
    'status': 'success',
    'taskId': 46890,
    'mocId': 328,
    'cdtId': 593,
    'validation_passed': True,
    'errors': [],
    'report': {
        'start_time': '2026-06-18 12:00:00',
        'end_time': '2026-06-18 12:05:00',
        'duration_seconds': 300,
        'moc_name': 'DLBSCTPCFG',
        'service_name': 'PcfPolicyEngineService',
        'commands': [
            {
                'name': 'SET DLBSCTPCFG',
                'type': 'update',
                'params': ['BUFFTYPE', 'ALARMSW', 'HIGHTSTR', 'HIGHTEND', 'OVERLOADSTR', 'OVERLOADEND'],
                'branches': [
                    {
                        'switchField': 'ALARMSW',
                        'triggerValue': 0,
                        'triggerName': 'ON',
                        'childFields': ['HIGHTSTR', 'HIGHTEND', 'OVERLOADSTR', 'OVERLOADEND']
                    }
                ]
            },
            {
                'name': 'LST DLBSCTPCFG',
                'type': 'get-config',
                'params': ['BUFFTYPE', 'ALARMSW', 'HIGHTSTR', 'HIGHTEND', 'OVERLOADSTR', 'OVERLOADEND'],
                'branches': []
            }
        ],
        'fields': [
            {'name': 'BUFFTYPE', 'type': 'ENUM', 'isKey': True, 'range': 'SENDBUFF(0),RECVBUFF(1)'},
            {'name': 'ALARMSW', 'type': 'ENUM', 'isKey': False, 'range': 'ON(0),OFF(1)'},
            {'name': 'HIGHTSTR', 'type': 'UINT32', 'isKey': False, 'range': '1~100'},
            {'name': 'HIGHTEND', 'type': 'UINT32', 'isKey': False, 'range': '1~100'},
            {'name': 'OVERLOADSTR', 'type': 'UINT32', 'isKey': False, 'range': '1~100'},
            {'name': 'OVERLOADEND', 'type': 'UINT32', 'isKey': False, 'range': '1~100'}
        ],
        'default_records': [
            {'BUFFTYPE': 'SENDBUFF', 'ALARMSW': 'ON', 'HIGHTSTR': '50', 'HIGHTEND': '40', 'OVERLOADSTR': '70', 'OVERLOADEND': '60'},
            {'BUFFTYPE': 'RECVBUFF', 'ALARMSW': 'ON', 'HIGHTSTR': '50', 'HIGHTEND': '40', 'OVERLOADSTR': '70', 'OVERLOADEND': '60'}
        ],
        'mr_url': 'https://codehub-y.huawei.com/UPCF/ComConfig/merge_requests/6485',
        'git_branch': 'feature/DLBSCTPCFG',
        'changed_files': ['PcfPolicyEngineService/om/cfg/microservice/input/modules/PCFPES/DLBSCTPCFG.xml', ...]
    }
}
```

## 执行报告说明

工作流执行完成后会自动生成执行报告，包含以下信息：

### 报告结构

| 字段 | 说明 |
|------|------|
| start_time | 任务开始时间 (YYYY-MM-DD HH:MM:SS) |
| end_time | 任务结束时间 (YYYY-MM-DD HH:MM:SS) |
| duration_seconds | 执行耗时（秒） |
| moc_name | 原子对象名称 |
| service_name | 服务名称 |
| commands | 创建的命令列表 |
| fields | 创建的字段列表 |
| default_records | 添加的默认记录（如果有） |
| mr_url | MR链接 |
| git_branch | Git分支名 |
| changed_files | 变更文件列表 |

### commands结构

| 字段 | 说明 |
|------|------|
| name | 命令名称（如SET DLBSCTPCFG） |
| type | 命令类型（create/delete/update/get-config） |
| params | 参数列表 |
| branches | 条件分支配置 |

### branches结构

| 字段 | 说明 |
|------|------|
| switchField | 触发条件的字段名 |
| triggerValue | 触发条件的枚举值 |
| triggerName | 触发条件的枚举项名称 |
| childFields | 条件满足时显示的字段列表 |

### default_records结构

每行默认记录是一个字典，key为字段名，value为该字段的默认值。

### 报告输出位置

报告会在控制台输出，同时包含在返回结果的`report`字段中。日志文件也会保存完整报告，位于`scripts/workflow_output.log`。

## 相关脚本

| 脚本 | 功能 |
|------|------|
| workflow.py | 主工作流入口 |
| omres_cli.py | omres-cli 定位与调用的统一封装（`find_omres_cli` / `run_cli`） |
| s01_login.py | 登录态校验（`ensure_authenticated`，只校验不登录） |
| s15_mml_commands.py | MML命令管理 |
| s12_manage_methods.py | 方法管理 |
| s17_validation.py | 校验与导出 |
| s18_add_errorcode.py | 错误码与Lua脚本管理 |

## 错误码配置

### error_codes参数说明

在execute_workflow中可以通过error_codes参数添加错误码和关联Lua脚本。配置示例：

```python
error_codes=[
    {
        "code": "ZL_58321",
        "code_num": 58321,
        "descCh": "缓冲区阈值冲突",
        "descEn": "Buffer threshold conflict"
    }
]
```

### 处理流程

1. 调用 `omres-cli info-code add` 添加错误码到OM工具
2. 调用 `omres-cli moc insert-info` 将MOC对象与Lua脚本关联
3. 调用 `omres-cli moc generate-script` 生成Lua脚本文件

### Lua脚本关联

- `omres-cli moc insert-info` 会将MOC对象与指定名称的Lua脚本关联
- `omres-cli moc generate-script` 会根据关联信息生成Lua脚本文件
- `script_operations`参数指定Lua脚本关联的操作类型（SET/LST/ADD/RMV）
- 只对非LST操作生成Lua脚本（如SET、ADD、RMV），LST操作不参与生成
- 多个操作类型用逗号分隔，一次API调用完成（如"SET,ADD"）

### Lua脚本业务逻辑与仓库同步

当配置了error_codes后，workflow会自动：
1. 调用API生成Lua脚本模板
2. 添加业务逻辑（校验规则、错误码定义）
3. 拷贝Lua脚本到代码仓库对应目录

**目标目录格式**：
```
{serviceName}/om/cfg/microservice/code/modules/{serviceShort}/lua/{mocName}.lua
```

**示例**：
- serviceName: `PcfDiamLoadBalanceService`
- serviceShort: `PCFDLB`
- mocName: `DLBSCTPBUFFCFG`
- 目标路径: `PcfDiamLoadBalanceService/om/cfg/microservice/code/modules/PCFDLB/lua/DLBSCTPBUFFCFG.lua`

### 自动生成的业务逻辑

当检测到字段包含`HIGHTSTR、HIGHTEND、OVERLOADSTR、OVERLOADEND`时，自动添加阈值校验：
- 校验规则: `HIGHTEND <= HIGHTSTR <= OVERLOADEND <= OVERLOADSTR`
- 校验失败时返回配置的error_code
