"""
[Skill-15/16] MML命令和参数管理
通过 omres-cli mml-command upsert / get、command-para upsert / list、mml-para list、command-branch upsert 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING, Dict, Any, List, Optional
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli, run_cli as _run_cli

if TYPE_CHECKING:
    from typing import Optional


def create_mml_command(
    context: WorkflowContext,
    mmlCommandName: str,
    commandType: str = "Add",
    service: str = "PcfNrfClientService",
    descCh: str = None,
    descEn: str = None,
    userGroup: str = "管理员级别,操作员级别",
    tableDescCh: str = "结果如下",
    tableDescEn: str = "The result is as follows",
    commandId: int = None,
    mmlCommandId: int = None,
    moduleId: int = None
) -> dict:
    """
    创建或更新MML命令 — 通过omres-cli mml-command upsert

    Args:
        context: 工作流上下文
        mmlCommandName: MML命令名称
        commandType: 命令类型 (Add/Remove/Modify/Lst)
        service: 服务名称
        descCh: 中文描述
        descEn: 英文描述
        userGroup: 用户组
        tableDescCh: 表描述中文
        tableDescEn: 表描述英文
        commandId: 命令ID (更新时使用)
        moduleId: 模块ID

    Returns:
        dict: 包含commandId的响应
    """
    task_id = context.get_required_state("taskId")
    mocId = context.get_required_state("mocId")
    moduleId = moduleId or context.moduleId or 5

    # 使用传入的mmlCommandId，如果没有则设为0让系统自动分配
    valid_mml_cmd_id = mmlCommandId if mmlCommandId else 0

    payload = {
        "mmlCommandTable": {
            "mmlCommandName": mmlCommandName,
            "service": service,
            "descEn": descEn or mmlCommandName,
            "descCh": descCh or mmlCommandName,
            "userGroupArr": userGroup.split(",") if isinstance(userGroup, str) else userGroup,
            "userGroup": userGroup,
            "applyNeArr": [],
            "applyNe": "",
            "tableDescCh": tableDescCh,
            "tableDescChSel": False,
            "tableDescEn": tableDescEn,
            "isHint": "否",
            "hintCh": "",
            "hintChSel": False,
            "hintEn": "",
            "isMultiCast": 0,
            "isSupportPartSuccess": 0,
            "commandOperationLogType": "",
            "serviceTypeIsMust": "",
            "serviceInstanceIsMust": "",
            "hintWarningCh": "",
            "hintWarningChSel": False,
            "hintWarningEn": "",
            "meaningCh": descCh or mmlCommandName,
            "meaningEn": descEn or mmlCommandName,
            "isHasGraphic": "否",
            "graphicId": "",
            "graphicEn": "",
            "graphicCh": "",
            "useExampleCh": "1",
            "useExampleChSel": False,
            "useExampleEn": "1",
            "warningCh": "",
            "warningChSel": False,
            "warningEn": "",
            "cmdExampleCh": "1",
            "cmdExampleChSel": False,
            "cmdExampleEn": "1",
            "referenceCh": "",
            "referenceEn": "",
            "effectCh": "" if commandType == "Lst" else "该命令执行后立即生效。",
            "effectEn": "" if commandType == "Lst" else "This command takes effect immediately.",
            "definitiontext": "",
            "definitionEn": "",
            "definitionService": service,
            "isMmlAuth": 0,
            "bypassStatus": "NO",
            "authTipsCh": "",
            "authTipsChSel": False,
            "authTipsEn": "",
            "centralConfig": 0,
            "upgradeBlock": 0,
            "outputItemDescriptionCh": "",
            "outputItemDescriptionEn": "",
            "hotBackupChkCmd": 0,
            "hotBackupChkCmdId": None,
            "isSupportRestExec": None,
            "defaultValueQueryCmd": "",
            "isSupportMTCenter": 0,
            "commandProcess": "ACS配置",
            "commandType": commandType,
            "mmlCommandId": valid_mml_cmd_id,
            "mocId": mocId,
            "preParam": "ACS(ACS):NETCONF_YANG",
            "innerCommandType": 0,
            "isCustom": 0,
            "moduleId": moduleId
        },
        "mocId": mocId,
        "taskId": task_id,
        "hotBackupChkCmd": 0,
        "hotBackupChkCmdId": None
    }

    # 确保id和mmlCommandId都正确设置
    if commandId:
        payload["mmlCommandTable"]["id"] = commandId
        payload["id"] = commandId
    if mmlCommandId:
        payload["mmlCommandTable"]["mmlCommandId"] = mmlCommandId

    # 如果service为空或None，不设置该字段，让系统使用模块默认值
    if service:
        payload["mmlCommandTable"]["service"] = service
    elif "service" in payload["mmlCommandTable"]:
        del payload["mmlCommandTable"]["service"]

    body = json.dumps(payload, ensure_ascii=False)
    print(f"  [DEBUG] create_mml_command: mmlCommandName={mmlCommandName}, commandType={commandType}")

    result = _run_cli(
        context,
        ["mml-command", "upsert", "--body", body],
        step_name="create_mml_command",
        timeout=60
    )

    # 验证更新是否成功 - 查询MML命令详情
    verify_body = json.dumps({"mmlCommandId": commandId or mmlCommandId, "taskId": task_id}, ensure_ascii=False)
    try:
        verify_result = _run_cli(
            context,
            ["mml-command", "get", "--body", verify_body],
            step_name="create_mml_command_verify",
            timeout=60
        )
        verify_data = verify_result.get("data", {})
        print(f"  [DEBUG] create_mml_command验证查询:")
        print(f"    - service: {verify_data.get('service')}")
        print(f"    - descCh: {verify_data.get('descCh')}")
        print(f"    - descEn: {verify_data.get('descEn')}")
        print(f"    - mmlCommandId: {verify_data.get('mmlCommandId')}")
    except StepExecutionError:
        pass  # 验证查询失败不影响主流程

    context.set_state("mml_command_created", True)

    return result


def add_command_para(
    context: WorkflowContext,
    mmlParaName: str,
    fieldId: int = None,
    fieldName: str = None,
    isMust: str = "可选",
    typeInWeb: str = "输入参数",
    rangeStr: str = None,
    customizeDataTypeId: int = None,
    dataTypeId: int = 1,
    defaultValue: str = "",
    commandId: int = None,
    mocName: str = None
) -> dict:
    """
    添加MML命令参数 — 通过omres-cli command-para upsert
    辅助查询通过 mml-para list / command-para list 实现

    Args:
        context: 工作流上下文
        mmlParaName: 参数名称
        fieldId: 字段ID
        fieldName: 字段名称 (用于查找fieldId)
        isMust: 是否必填 ("必选"/"可选")
        typeInWeb: 参数类型 ("输入参数"/"输出参数")
        rangeStr: 取值范围
        customizeDataTypeId: 自定义类型ID (枚举类型用)
        dataTypeId: 数据类型ID
        defaultValue: 默认值
        commandId: 命令ID
        mocName: 原子对象名称

    Returns:
        dict: 添加结果
    """
    task_id = context.get_required_state("taskId")
    commandId = commandId or context.get_required_state("commandId")
    mocName = mocName or context.moc_name or context.get_state("mocName")

    # 如果没有提供fieldId，从field_map中查找
    if not fieldId and fieldName:
        field_map = context.get_state("field_map", {})
        fieldId = field_map.get(fieldName)

    if not fieldId:
        raise StepExecutionError(
            step_name="add_command_para",
            message=f"未找到字段ID: {fieldName}",
            context_state=context.state
        )

    # 获取mmlParaId - 通过omres-cli mml-para list查询
    mml_para_id = None
    moc_id = context.get_required_state("mocId")
    query_para_body = json.dumps({"taskId": task_id, "mocId": moc_id}, ensure_ascii=False)
    query_para_result = _run_cli(
        context,
        ["mml-para", "list", "--body", query_para_body],
        step_name="add_command_para_query_mml_para",
        timeout=60
    )
    if isinstance(query_para_result, dict):
        para_list = query_para_result.get("data", [])
        print(f"  [DEBUG] mmlPara list共返回 {len(para_list)} 条记录")
        for para in para_list:
            if str(para.get("fieldId")) == str(fieldId):
                mml_para_id = para.get("id")
                print(f"  [DEBUG] 找到mmlParaId: {mml_para_id} for fieldId: {fieldId}")
                break

    if not mml_para_id:
        raise StepExecutionError(
            step_name="add_command_para",
            message=f"未找到字段{fieldId}对应的mmlParaId",
            context_state=context.state
        )

    payload = {
        "commandParaTable": {
            "mmlParaId": mml_para_id,
            "isMust": isMust,
            "typeInWeb": typeInWeb,
            "customParaRange": "",
            "range": rangeStr or "",
            "customizeDataTypeId": customizeDataTypeId if customizeDataTypeId else -1,
            "defaultValue": defaultValue,
            "associateMocId": None,
            "associateFieldId": None,
            "customedAssociateMocName": None,
            "customedAssociateFieldName": None
        },
        "fieldId": fieldId,
        "taskId": task_id,
        "commandId": commandId,
        "mmlParaName": mmlParaName,
        "mmlDataType": dataTypeId,
        "mocName": mocName
    }

    body = json.dumps(payload, ensure_ascii=False)
    print(f"  [DEBUG] add_command_para: mmlParaName={mmlParaName}, fieldId={fieldId}, commandId={commandId}")

    result = _run_cli(
        context,
        ["command-para", "upsert", "--body", body],
        step_name="add_command_para",
        timeout=60
    )

    # 查询命令参数列表确认是否添加成功
    list_body = json.dumps({"taskId": task_id, "commandId": commandId}, ensure_ascii=False)
    try:
        list_result = _run_cli(
            context,
            ["command-para", "list", "--body", list_body],
            step_name="add_command_para_verify",
            timeout=60
        )
        print(f"  [DEBUG] commandPara/list查询完成")
    except StepExecutionError:
        pass  # 验证查询失败不影响主流程

    context.set_state(f"para_added_{mmlParaName}", True)

    return result


def select_by_id_mml_command(
    context: WorkflowContext,
    mmlCommandId: int = None,
    methodId: int = None
) -> dict:
    """
    通过mmlCommandId或methodId查询MML命令详情 — 通过omres-cli mml-command get

    Args:
        context: 工作流上下文
        mmlCommandId: MML命令ID
        methodId: 方法ID (二选一)

    Returns:
        dict: 包含mmlCommandId的响应
    """
    task_id = context.get_required_state("taskId")

    # 优先使用mmlCommandId，否则用methodId
    cmd_id = mmlCommandId or methodId

    body = json.dumps({
        "mmlCommandId": cmd_id,
        "taskId": task_id
    }, ensure_ascii=False)

    print(f"  [DEBUG] select_by_id_mml_command: mmlCommandId={cmd_id}")

    result = _run_cli(
        context,
        ["mml-command", "get", "--body", body],
        step_name="select_by_id_mml_command",
        timeout=60
    )

    # 从响应中提取mmlCommandId并保存到context
    data = result.get("data", {})
    returned_mml_cmd_id = data.get("mmlCommandId") or data.get("id")

    if returned_mml_cmd_id:
        context.set_state("mmlCommandId", returned_mml_cmd_id)
        print(f"  [DEBUG] select_by_id_mml_command获取到mmlCommandId: {returned_mml_cmd_id}")

    return result


def add_command_branch(
    context: WorkflowContext,
    switchCommandParaId: int,
    switchEnumItemId: int,
    childCommandParaDtos: List[Dict[str, Any]] = None,
    commandId: int = None
) -> dict:
    """
    添加命令分支(条件参数) — 通过omres-cli command-branch upsert

    Args:
        context: 工作流上下文
        switchCommandParaId: 切换参数ID
        switchEnumItemId: 枚举项ID
        childCommandParaDtos: 子参数列表
        commandId: 命令ID

    Returns:
        dict: 添加结果
    """
    task_id = context.get_required_state("taskId")
    commandId = commandId or context.get_required_state("commandId")

    payload = {
        "taskId": task_id,
        "commandId": commandId,
        "commandBranchTableId": [],
        "switchCommandParaId": switchCommandParaId,
        "switchCommandParaExtendName": "",
        "switchEnumItemId": switchEnumItemId,
        "childCommandParaDtos": childCommandParaDtos or []
    }

    body = json.dumps(payload, ensure_ascii=False)
    print(f"  [DEBUG] add_command_branch: switchCommandParaId={switchCommandParaId}, switchEnumItemId={switchEnumItemId}")

    result = _run_cli(
        context,
        ["command-branch", "upsert", "--body", body],
        step_name="add_command_branch",
        timeout=60
    )

    return result


if __name__ == "__main__":
    from context import create_context
    ctx = create_context(taskName="TEST", userName="test", moduleId=5)
    print("MML命令模块测试需要完整流程")
