---
name: upcf-mml-e2e
description: 实现UPCF MML端到端开发
allowed-tools: Bash Read Write Edit Glob Grep Skill Agent
permissionMode: bypassPermissions
---

[e2e-mode]
**已激活 E2E 端到端模式。全生命周期多仓开发工作流。**

---

## 总览

本流程分为 **准备阶段 + 六个阶段**，严格按顺序执行：

0. **准备阶段** – 主代理串行完成：初始化与鉴权引导 → 收集用户输入 → 拉取分支 → 下载代码仓 → 生成需求文档。
1. **编码阶段** – 主代理并行委派两个 `upcf-mml-coding-agent` 子代理，分别开发 MML 代码仓和 MML 建模仓。
2. **门禁编译阶段** – 主代理委派 `upcf-mml-gated-agent` 子代理，由其加载 `pipeline-gated`，完成 MR 检查 / 创建、continuous 门禁支持性检查、门禁状态查询、修复文件上传和编译结果写入。
3. **出包阶段** – 主代理委派 `upcf-mml-package-agent` 子代理，触发出包任务。
4. **部署阶段** – 主代理委派 `upcf-mml-deployment-agent` 子代理，触发部署任务。
5. **ST 阶段** – 主代理依次委派 `upcf-mml-st-design-agent`（测试设计）、`upcf-mml-st-gen-agent`（分批串行脚本生成）和 `upcf-mml-st-exec-agent`（分批串行脚本执行+批次报告）三个子代理，最后由主代理汇总最终报告。
6. **MR 提交阶段** – 主代理更新阶段二已创建 / 复用的 MR 描述。

## 本流程使用的任务

出包阶段使用网元配置中的 **版本包** 任务。

---

## 阶段零：准备

> 主代理串行执行以下各步，前一步未通过不进入下一步。
> 前几步（鉴权、收集用户输入）需要用户亲自参与，必须在主 session 完成。

### Step 1：初始化与 CLI 鉴权引导

先创建工作目录：确保当前工作目录下存在 `.workspace/`（存放代码仓）与 `.e2e_files/`（存放阶段产物），不存在则创建，并确认二者已在 `.gitignore` 中。

然后依次校验 `pipelinex-cli`、`codehub-cli`、`omres-cli` 的安装与认证。登录需用户亲自操作，主代理只做检测与引导，**绝不代为输入账号密码或 token，也不得把凭证写入任何文件或提示词**。

**pipelinex-cli：**
1. 运行 `pipelinex-cli` 检测安装。未安装则：下载 `http://7.183.28.77:9155/pipeline-x-arti/pipeline-x-hlt/pipelinex-cli.exe?download=true` 到 `%USERPROFILE%\pipelinex-cli\`，用 `setx PATH "%USERPROFILE%\pipelinex-cli;%PATH%"` 写入用户环境变量 PATH（供子代理与后续会话使用）。**当前会话主代理调用 pipelinex-cli 时用全路径 `%USERPROFILE%\pipelinex-cli\pipelinex-cli.exe`**，因为系统 PATH 的更新对已启动的当前会话不生效。下载失败则提示用户手动下载并配置 PATH，等待用户确认后重新检测。
2. 运行 `pipelinex-cli auth status` 检测认证（同上，当前会话用全路径）。未认证 → 提示用户执行 `pipelinex-cli auth login --token <token>`，给出「已完成 / 取消」选项：已完成则复检，取消则退出流程。

**codehub-cli：**
1. 运行 `codehub-cli --version` 检测安装。未安装则按以下命令安装，完成后重新检测：
   ```bash
   npm config set strict-ssl false
   npm config set @codehub:registry=https://cmc.centralrepo.rnd.huawei.com/artifactory/api/npm/product_npm/
   npm install -g @codehub/codehub-cli
   ```
2. 运行 `codehub-cli auth status` 检测认证。未认证 → 提示用户执行 `codehub-cli auth login`，给出「已完成 / 取消」选项：已完成则复检，取消则退出流程。

**omres-cli：**（OMResTool 建模服务，供编码阶段的建模 skill 调用）

1. 运行 `omres-cli version` 检测安装。未安装则：下载 `http://7.183.28.77:9155/omres-cli-arti/omres-cli-product/omres-cli.exe` 到 `%USERPROFILE%\omres-cli\`，用 `setx PATH "%USERPROFILE%\omres-cli;%PATH%"` 写入用户环境变量 PATH。**当前会话主代理调用时用全路径 `%USERPROFILE%\omres-cli\omres-cli.exe`**（原因同 pipelinex-cli）。下载失败则提示用户手动下载并配置 PATH，等待用户确认后重新检测。

2. 运行 `omres-cli auth status` 检测认证。**该命令用退出码表达状态，主代理据此分支，不要靠解析文案：**

   | 退出码 | 含义 | 主代理动作 |
      |--------|------|-----------|
   | 0 | 已认证 | 进入下一步 |
   | 3 | 未认证 / 会话已过期 | 走下面的登录引导 |
   | 1 | 其它错误（后端不可达等） | **不是**认证问题，转达错误并停止，等用户处理网络或服务端 |

3. 退出码为 3 时，提示用户在**自己的终端**里执行下列任一方式登录，给出「已完成 / 取消」选项：已完成则重新执行 `omres-cli auth status` 复检，取消则退出流程。

   ```powershell
   # 交互式（推荐，密码不回显）
   omres-cli auth login --username <域账号>

   # 非交互（密码走标准输入，不进命令历史）
   omres-cli auth login --username <域账号> --password-stdin

   # CI / 已注入环境变量时
   omres-cli auth login
   ```

   **主代理禁止代替用户执行 `auth login`，禁止向用户索要密码，禁止把密码写进 prompt、日志或任何文件。**

4. 需要确认会话在后端真实有效（而不只是本地存在）时，用 `omres-cli auth status --online`：它会额外发一次只读探活请求。退出码语义同上。

三个 CLI 均认证通过后，进入下一步。

> **会话有效期说明**：`omres-cli` 登录态保存在 `%USERPROFILE%\.omres-cli\session.json`（权限 0600），后端未下发过期时间时按默认 8 小时判定（可用 `OMRES_SESSION_TTL_HOURS` 覆盖）。E2E 全流程较长，若中途某个子代理报 omres 未认证，按「重要注意事项」中的规则处理，**不得由主代理自行重登**。

### Step 2：收集用户输入

本流程需要用户提供以下信息。**逐项向用户索要，未提供则停下等待用户在下一条消息中给出，不猜测、不使用默认值。** 收集到的值由主代理记在上下文中，后续步骤通过委派 prompt 传递，不写入任何文件。

- **本次开发的新分支名**：提示「请输入本次开发的新分支名」。用于拉取分支时创建并切换到该分支，门禁阶段作为 MR 源分支。
- **本次开发的基线分支**：提示「请输入本次开发的基线分支」。用于拉取分支时作为新分支的基线，门禁阶段作为 MR 目标分支。
- **本次部署的环境 ID**：提示「请输入本次部署的环境 ID」。用于部署阶段。

三项均收齐后，进入下一步。

### Step 3：拉取分支

主代理调用 `create-branch` skill（隔离执行），传入新分支名、基线分支。仅在 GoldenTower 远端创建分支。

- ✅ 成功 → 进入下一步
- ❌ 失败 → 转达失败原因并停止

### Step 4：下载代码仓

主代理调用 `download-repo` skill（隔离执行），传入以下仓库地址、目标目录 `.workspace/`、目标分支（本次开发的新分支名）。skill 会 clone 下面的仓库到目标目录下并切到新分支：

- `https://szv-y.codehub.huawei.com/UPCF/UPCFDLB.git`
- `https://szv-y.codehub.huawei.com/UPCF/ComConfig.git`
- `https://codehub-dg-y.huawei.com/UPCF_TEST/UPCFTestcases.git`

此后各阶段的代码仓路径均以 `.workspace/{仓名}` 为准。

- ✅ 仓库就绪 → 进入下一步
- ❌ 失败 → 转达失败原因并停止

### Step 5：需求文档生成

读取用户输入的需求编号，调用 `corealm-explore` skill 生成需求文档。告知用户获取成功，进入下一阶段。

---

---

## 阶段一：编码（并行多仓开发）

### 操作步骤

1. **读取需求**：读取 `.e2e_files/需求文档.md`，确认需求内容。

2. **并行委派编码任务**：为每个仓分别创建 `task`，设置 `run_in_background=true`。

MML 代码仓为：UPCFDLB（当前仓）  
MML 建模仓为：ComConfig  
测试代码仓为：UPCFTestcases

SKILL使用：
UPCFDLB开发需要用到upcfdlb-mml-object-generator skill
ComConfig开发需要用到upcf-add-command skill、pcf-lua-generate
所以在委派task时，必须prompt中指定skill_to_use，不要使用load_skills参数来注入skill，直接在prompt中指定就行。

> ComConfig 建模相关 skill 依赖 `omres-cli` 的登录态。该登录已在阶段零 Step 1 完成，会话文件对所有子代理进程可见（同一用户主目录），子代理**直接调用 `omres-cli` 即可，无需也不得再次登录**。若子代理检测到未认证，应直接返回失败并说明原因，交由主代理向用户求助。

> 建模仓里已经存在同名对象（如 `modules/PCFDLB/XXX.xml`）是**正常情况**：上传解析会把它导入工程，`upcf-add-command` 会自动复用该对象做增量建模。子代理**不得**为此改名、换工程或另写「跳过 create_moc」的自定义脚本，按 skill 的标准入口 `execute_workflow` 执行即可。

代码仓路径：
task委派时，prompt字段中需要指定code_path，代码仓位于当前工作目录的 `.workspace/` 下，即 `.workspace/UPCFDLB`、`.workspace/ComConfig`。

1、给UPCFDLB仓委派一个任务，指定skill为upcfdlb-mml-object-generator
2、给ComConfig委派一个任务，指定skill为upcf-add-command
3、再次给ComConfig委派一个任务，指定skill为pcf-lua-generate
task调用格式如下：
```javascript
Agent(
  subagent_type="upcf-mml-coding-agent",
  prompt="\nrepo_name: UPCFDLB\nrequirements_path: .e2e_files/需求文档.md\nskill_to_use: upcfdlb-mml-object-generator\ncode_path: .workspace/UPCFDLB\n",
  run_in_background=true,
  description="MML代码仓开发"
)

Agent(
  subagent_type="upcf-mml-coding-agent",
  prompt="\nrepo_name: ComConfig\nrequirements_path: .e2e_files/需求文档.md\nskill_to_use: upcf-add-command\ncode_path: .workspace/ComConfig\n",
  run_in_background=true,
  description="MML建模仓开发"
)

Agent(
  subagent_type="upcf-mml-coding-agent",
  prompt="\nrepo_name: ComConfig\nrequirements_path: .e2e_files/需求文档.md\nskill_to_use: pcf-lua-generate\ncode_path: .workspace/ComConfig\n",
  run_in_background=true,
  description="lua脚本生成"
)
```

3. **等待完成**：结束当前回复，等待通知。
4. **获取结果**：获取每个任务的输出（包括提交哈希等）。

告知用户编码结果摘要，进入下一阶段。

---

## 阶段二：门禁编译（MR 准备 → continuous 门禁）

> 用途：完成 UPCFDLB / ComConfig 的 MR 检查或创建，再基于 MR 触发 continuous gated 门禁编译，快速验证代码改动。

**委派门禁编译子代理**（`{新分支名}` 用前面收集的新分支名替换，`{基线分支}` 用前面收集的基线分支替换）：

```javascript
Agent(
  subagent_type = "upcf-mml-gated-agent",
  prompt = "开始 MML 门禁编译\n门禁目标仓：https://szv-y.codehub.huawei.com/UPCF/UPCFDLB.git\n附加准备仓：https://szv-y.codehub.huawei.com/UPCF/ComConfig.git\n源分支：{新分支名}\n目标分支：{基线分支}\n需求文档：.e2e_files/需求文档.md\nMR信息文件：.e2e_files/mr_info.jsonc\n门禁结果文件：.e2e_files/编译结果.jsonc\n门禁日志目录：.e2e_files/logs/build\n修复日志目录：.e2e_files/logs/fix\nUPCFDLB本地仓：.workspace/UPCFDLB\nComConfig本地仓：.workspace/ComConfig",
  run_in_background = true,
  description = "MML门禁编译-MR准备与continuous门禁"
)
```

委派后立即结束当前回复，等待通知。收到通知后获取子代理输出，确认以下文件已更新：

```text
.e2e_files/mr_info.jsonc
.e2e_files/编译结果.jsonc
```

- ✅ 成功（含修复后成功）→ 展示门禁摘要，进入下一阶段
- ❌ 失败 → 展示失败原因与已尝试的修复，停在本阶段等待用户指令

---

## 阶段三：出包

**委派出包子代理**：

```javascript
Agent(
  subagent_type = "upcf-mml-package-agent",
  prompt = "开始出包\n任务名：版本包\n结果文件：.e2e_files/出包结果.json",
  run_in_background = true,
  description = "出包-流水线构建"
)
```

- ✅ 成功且拿到 CMC 产物地址 → 展示出包摘要，**记录出包结果文件路径**（部署阶段要用），进入下一阶段
- ❌ 失败，或成功但未提取到产物地址 → 展示原因，停在本阶段等待用户指令

---

## 阶段四：部署

**委派部署子代理**（`{环境ID}` 使用用户输入的值替换，`{出包结果文件路径}` 用出包阶段的值替换）：

```javascript
Agent(
  subagent_type = "upcf-mml-deployment-agent",
  prompt = "开始部署\n环境ID：{环境ID}\n产物结果文件：{出包结果文件路径}\n结果文件：.e2e_files/部署结果.json",
  run_in_background = true,
  description = "部署-珊瑚流水线"
)
```

- ✅ 成功 → 展示部署摘要，进入下一阶段
- ❌ 失败 → 展示失败原因，停在本阶段等待用户指令

---

## 阶段五：ST（依次委派三个子代理）

> **ST阶段拆分为三个子代理串行执行，以控制上下文长度。**
> - `upcf-mml-st-design-agent`：测试设计（需求解析→测试点→测试用例→用例检查）
> - `upcf-mml-st-gen-agent`：脚本生成（分批串行）
> - `upcf-mml-st-exec-agent`：脚本执行+报告（分批串行）
    > **执行机同一时刻只能运行一个测试脚本。详细串行约束已在子代理的 system_instructions 中定义，主代理在 prompt 中只需强调关键点即可。**

### 分批通用规则（Step 2/3 共用）

- **每批数量**：≤3个（用例或脚本）
- **批次计算**：`batch_count = ceil(N / 3)`
- **异步模式**：委派后立即结束回复，等待通知后获取结果
- **失败处理**：某批次失败仅重跑该批次，不影响其他批次；如因上下文超限失败，考虑进一步缩小批次数量

### Step 1：委派测试设计子代理

```javascript
Agent(
  subagent_type="upcf-mml-st-design-agent",
  prompt="\n需求设计文档的路径为.e2e_files/需求文档.md\nST阶段代码仓为UPCFTestcases测试代码仓\ncode_path: .workspace/UPCFTestcases",
  run_in_background=true,
  description="ST-测试设计"
)
```

完成后确认 `.e2e_files/design_output/test_case_spec/*fix*.json` 已生成，进入下一步。

### Step 2：委派脚本生成子代理（分批串行）

1. **清理E2E残留（仅首批前执行一次）**：在委派第1批前，用bash工具逐条执行以下3条命令（`workdir` 均为 `.workspace/UPCFTestcases`）：
   - `git checkout -- UserFiles/Logic/`
   - `git clean -fd UserFiles/Logic/`
   - `git status --porcelain UserFiles/Logic/`

   注意：bash 工具可能会自动注入 PowerShell 环境变量前缀导致报 14 行 `command not found` warning，**这不影响后续 git 命令执行**，忽略即可。判断清理是否成功的依据：最后一条 `git status` 的输出中（排除 warning 行）若无 git 相关输出即清理成功；若有 fatal 或 error 则关注，否则视为成功。**严禁重试**。后续批次不再执行此清理。

2. **预处理**：读取 `.e2e_files/design_output/test_case_spec/` 下 `*fix*.json` 文件，提取文件名前缀（即去掉 `_test_case_fix_spec.json` 后的部分，如 `DLBSCTPBUFFCCC`），按每批≤3个用例拆分为 `{前缀}_test_case_batch_{i}.json`，写入同一目录。

3. **串行委派**：逐批委派，**必须等前一批通知到达后再委派下一批**。

```javascript
Agent(
  subagent_type="upcf-mml-st-gen-agent",
  prompt="\n按需加载需求设计文档，路径为 .e2e_files/需求文档.md\n测试用例文件路径为 .e2e_files/design_output/test_case_spec/{前缀}_test_case_batch_{i}.json\n本批为第{i}批，共需生成 {本批用例数} 个脚本\nST阶段代码仓为UPCFTestcases测试代码仓\ncode_path: .workspace/UPCFTestcases\n",
  run_in_background=true,
  description="ST-脚本生成-批次{i}"
)
```

全部批次成功后，确认 `.e2e_files/design_output/test-script-generator-result/` 下脚本已生成，进入下一步。

### Step 3：委派脚本执行+报告子代理（分批串行）

1. **预处理**：扫描 `.e2e_files/design_output/test-script-generator-result/` 下所有 .py 脚本（排除 AW 文件），按每批≤3个拆分，记录每批脚本文件名列表。

2. **串行委派**：逐批委派，**必须等前一批通知到达后再委派下一批**。

```javascript
Agent(
  subagent_type="upcf-mml-st-exec-agent",
  prompt="\n按需加载需求设计文档，路径为 .e2e_files/需求文档.md\n执行机信息按 ST 阶段自身约定获取\nST阶段代码仓为UPCFTestcases测试代码仓\ncode_path: .workspace/UPCFTestcases\n本批为第{i}批，共{batch_count}批\n本批需执行的脚本文件名列表：{本批脚本文件名列表}\n仅执行上述列表中的脚本，不要执行其他批次的脚本\n",
  run_in_background=true,
  description="ST-脚本执行-批次{i}"
)
```

3. **最终汇总**：所有批次完成后，主代理读取所有 `batch_*_report.md`，合并生成 `Final_Summary_Report.md`（整体统计求和、脚本结果拼接、问题去重合并、总体结论、待补测清单），确认生成后 ST 阶段完成。

ST 执行成功后，**自动进入阶段六**。

---

## 阶段六：MR 提交（更新 MR 描述）

> 阶段六不再创建 MR。
>
> MR 创建 / 复用已前移到阶段二“门禁编译”前完成。本阶段只更新阶段二记录在 `.e2e_files/mr_info.md` 中的 MR 描述。

首先使用 skill 工具加载 `codehub-skill`，学习如何使用 codehub 相关能力。

`codehub-cli` 已在阶段零完成安装与鉴权，此处直接更新 MR 描述。

### Step 1：读取阶段二 MR 信息

读取：

```text
.e2e_files/mr_info.md
```

获取 UPCFDLB 和 ComConfig 的：

- 项目路径
- Host
- 源分支
- 目标分支
- MR IID
- MR 链接
- MR 来源
- 是否用于门禁触发

若 `.e2e_files/mr_info.md` 不存在，或某个有代码改动的仓缺少 MR IID，则停止并报告：

```text
阶段六无法更新 MR 描述：未找到阶段二生成的 MR 信息。请确认阶段二是否已完成 MR 创建 / 复用。
```

### Step 2：读取阶段产物

读取以下文件并提取摘要：

| 内容 | 文件 |
|------|------|
| 需求概述 / 需求编号 | `.e2e_files/需求文档.md` |
| 编译结果 | `.e2e_files/编译结果.md` |
| 出包结果 / CMC URL | `.e2e_files/出包结果.json` |
| 部署结果 | `.e2e_files/部署结果.json` |
| ST 结果 | `.e2e_files/design_output/test-script-debugger-result/Final_Summary_Report.md` |

### Step 3：更新 UPCFDLB 和 ComConfig MR 描述

对 `.e2e_files/mr_info.md` 中存在 MR IID 的仓库分别执行 MR 描述更新。

命令格式：

```bash
codehub-cli mr update <mr-iid> \
  -p UPCF/ComConfig \
  -H https://codehub-dg-y.huawei.com \
  --description "【需求概述】xxx(从需求文档中提取关键信息) \n 【ST结果】xxx（从阶段五的报告中提取关键信息）"
```

入参需要根据实际情况进行调整，例如issue-nums可以在需求文档.md中找到。

确保UPCFDLB和ComConfig仓都提交MR。

### Step 4：输出 MR 更新摘要

更新完成后，输出：

| 仓库 | MR IID | MR 链接 | 更新结果 |
|------|--------|---------|----------|
| UPCFDLB | xxx | xxx | 成功 / 失败 |
| ComConfig | xxx | xxx | 成功 / 失败 |

若任一 MR 描述更新失败，停止并向用户报告失败原因，不得主观忽略。

---

所有阶段已完成后，总结各个阶段的输出件并告知用户。

## 异步执行规则（通用）

- **所有 `Agent()` 委派（含主代理委派子代理、子代理内部再委派子代理）一律异步**：设置 `run_in_background=true`，委派后立即结束回复，收到系统通知后再获取结果。
- 后台任务完全就绪前，**不得**进入下一阶段、下一轮或尝试获取结果。

---

## 重要注意事项

- 主代理必须严格按总览列出的阶段顺序执行，准备阶段内部也按 Step 顺序执行，不可打乱或跳过。
- 如果某个阶段成功完成，则自动进入下一阶段。
- **阶段结果以脚本/子代理的实际返回为准，主代理不得主观臆断“某步失败但不影响后续”而跳过或继续。** 任一阶段返回失败、报错或未确认成功时，主代理必须停止并向用户报告，由用户决定下一步。用户“跳过某阶段”的指令只免除该阶段的执行，不改变“未跳过的阶段一旦失败就停止”这一规则。**唯一例外是门禁编译阶段的自动修复重编，该机制由 upcf-mml-gated-agent 内部完成，主代理只接收最终结论。**
- **子代理失败时主代理只转达、不接手**：子代理返回任何失败（含权限受限、工具缺失、无法执行）时，主代理停止并向用户报告，**严禁在主 session 中改用 Bash/Skill 或直接运行该子代理本应执行的脚本来“替它完成”**。绕过隔离会使日志与源码涌入主上下文，破坏上下文控制。
- 编码阶段两个仓的 skill 不同，必须通过 prompt 中的 `skill_to_use` 区分。
- ST 阶段 upcf-mml-st-gen-agent 和 upcf-mml-st-exec-agent 的分批场景下，某批次失败仅重跑该批次（这是对同一批次的重跑，非“修复后重试”，与门禁修复机制性质不同，不改变“其他阶段失败即停”的总则）。
- task 的入参 prompt 不需要原始系统指令中要求的那么复杂，在端到端任务中，按照上面示例中我要求的格式写就好。
- **凭证由用户亲自输入，主代理只做检测与引导**：`pipelinex-cli` / `codehub-cli` / `omres-cli` 的登录一律由用户在自己的终端完成。主代理不得索要、转述、存储或代填任何账号、密码、token，也不得把它们写入 `.e2e_files/` 下的任何产物。
- **omres 会话中途失效的处理**：若任一子代理返回 omres 未认证（`auth status` 退出码 3 / 后端返回未登录），主代理停止当前阶段并提示用户重新执行 `omres-cli auth login`，用户确认后**从失败的那个阶段重跑**，不跳过、不由主代理代为登录。

---

**现在，请开始执行 E2E 流程。**
