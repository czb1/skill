"""
[Skill-18] 错误码与Lua脚本管理
通过 omres-cli info-module query-all、info-code add / list、moc insert-info / generate-script 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING, List, Dict, Any
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli, run_cli as _run_cli

if TYPE_CHECKING:
    from typing import Optional

FILE_DATABASE_ROOT_PATH = "D:/download/lua_scripts"


def query_info_module_id(context: WorkflowContext, service_name: str) -> int:
    """
    查询infoModuleId — 通过omres-cli info-module query-all

    Args:
        context: 工作流上下文
        service_name: 服务名称（如PcfDiamLoadBalanceService）

    Returns:
        int: infoModuleId
    """
    task_id = context.get_required_state("taskId")

    body = json.dumps({"projectId": task_id}, ensure_ascii=False)

    print(f"  [DEBUG] query_info_module_id: service_name={service_name}, projectId={task_id}")

    result = _run_cli(
        context,
        ["info-module", "query-all", "--body", body],
        step_name="query_info_module_id",
        timeout=60
    )

    module_list = result.get("data", [])
    for module in module_list:
        if module.get("moduleName") == service_name or module.get("serviceName") == service_name:
            info_module_id = module.get("id")
            print(f"  ✓ 找到 {service_name} 的infoModuleId: {info_module_id}")
            return info_module_id

    print(f"  ⚠ 未找到 {service_name}，使用默认moduleId")
    return context.moduleId


def add_error_code(
    context: WorkflowContext,
    infoCodeName: str,
    infoCodeChDesc: str,
    infoCodeEnDesc: str,
    infoModuleId: int = None
) -> dict:
    """
    添加错误码 — 通过omres-cli info-code add

    Args:
        context: 工作流上下文
        infoCodeName: 错误码名称（如ZL_58321）
        infoCodeChDesc: 错误码中文描述
        infoCodeEnDesc: 错误码英文描述
        infoModuleId: 模块ID（默认从上下文获取）

    Returns:
        dict: 添加结果
    """
    task_id = context.get_required_state("taskId")

    if infoModuleId is None:
        infoModuleId = context.moduleId

    body = json.dumps({
        "projectId": task_id,
        "infoCode": {
            "infoCodeName": infoCodeName,
            "isCustom": 0,
            "infoCodeChDesc": infoCodeChDesc,
            "infoCodeEnDesc": infoCodeEnDesc,
            "infoCodeNum": None,
            "infoModuleId": infoModuleId
        }
    }, ensure_ascii=False)

    print(f"  [DEBUG] add_error_code: infoCodeName={infoCodeName}, infoModuleId={infoModuleId}")

    result = _run_cli(
        context,
        ["info-code", "add", "--body", body],
        step_name="add_error_code",
        timeout=60
    )

    print(f"  ✓ 错误码 {infoCodeName} 添加成功")
    return result


def query_error_codes(context: WorkflowContext) -> dict:
    """
    查询错误码列表 — 通过omres-cli info-code list

    Args:
        context: 工作流上下文

    Returns:
        dict: 错误码列表
    """
    task_id = context.get_required_state("taskId")

    body = json.dumps({"projectId": task_id}, ensure_ascii=False)

    print(f"  [DEBUG] query_error_codes: projectId={task_id}")

    result = _run_cli(
        context,
        ["info-code", "list", "--body", body],
        step_name="query_error_codes",
        timeout=60
    )

    context.set_state("error_codes", result.get("data", []))
    return result


def insert_moc_info_with_lua(
    context: WorkflowContext,
    moc_name: str,
    lua_file_name: str,
    script_operations: List[str],
    moc_desc_ch: str = "",
    moc_desc_en: str = ""
) -> dict:
    """
    将MOC对象与Lua脚本关联 — 通过omres-cli moc insert-info

    Args:
        context: 工作流上下文
        moc_name: MOC对象名称
        lua_file_name: Lua脚本文件名（如DLBSCTPBUFFCFG.lua）
        script_operations: 关联的操作类型列表（如["SET", "LST"]）
        moc_desc_ch: MOC中文描述
        moc_desc_en: MOC英文描述

    Returns:
        dict: 关联结果
    """
    task_id = context.get_required_state("taskId")
    moc_id = context.get_state("mocId")

    set_operations = [op for op in script_operations if op.upper() == "SET"]
    if not set_operations:
        set_operations = ["SET"]
    script_oper_json = json.dumps(set_operations).replace(" ", "")
    script_config = json.dumps([{
        "scriptName": lua_file_name,
        "scriptType": "MAIN",
        "scriptOper": script_oper_json
    }], ensure_ascii=False)

    payload = {
        "maxRecords": None,
        "almEcoveryThreshold": None,
        "almThreshold": None,
        "mocId": moc_id,
        "batchDelete": 0,
        "maxRecordNum": 256,
        "isSuspendRemoteRes": 0,
        "mocTypeId": 2,
        "recUpgMode": "Auto",
        "passwordExportMode": "0",
        "objectId": None,
        "recCopyMode": "copyMode",
        "mocName": moc_name,
        "mocDescEn": moc_desc_en or moc_name,
        "m2k": 1,
        "mocDescCh": moc_desc_ch or moc_name,
        "minRecordNum": 0,
        "isAutoCpxcfg": 0,
        "version": "1.0",
        "publicMode": "inner",
        "script": script_config,
        "mocTypeName": "配置",
        "sceneReference": None,
        "isProcessReport": None,
        "blankMode": 0,
        "associatedComponentId": None,
        "taskId": str(task_id),
        "moduleId": str(context.moduleId),
        "w3Num": context.w3Num,
        "associateInfo": "",
        "subscribeModuleInfo": "",
        "oldMocName": ""
    }

    body = json.dumps(payload, ensure_ascii=False)

    print(f"  [DEBUG] insert_moc_info: moc_name={moc_name}, lua_file_name={lua_file_name}")

    result = _run_cli(
        context,
        ["moc", "insert-info", "--body", body],
        step_name="insert_moc_info",
        timeout=60
    )

    print(f"  ✓ MOC {moc_name} 与Lua脚本 {lua_file_name} 关联成功")
    return result


def generate_lua_script(
    context: WorkflowContext,
    moc_name: str,
    script_oper: str = "set",
    is_generate_code: int = 0,
    save_path: str = None
) -> str:
    """
    生成并下载Lua脚本文件 — 通过omres-cli moc generate-script

    Args:
        context: 工作流上下文
        moc_name: MOC对象名称
        script_oper: 脚本操作类型，多个操作用逗号分隔（如"SET,ADD"）
        is_generate_code: 是否生成代码（0或1）
        save_path: 保存路径，默认保存到 D:/download/lua_scripts/{taskId}/{moduleId}/{mocId}/generate/

    Returns:
        str: 保存的文件路径
    """
    task_id = context.get_required_state("taskId")
    moc_id = context.get_state("mocId")
    module_id = context.moduleId

    if save_path is None:
        save_path = os.path.join(FILE_DATABASE_ROOT_PATH, str(task_id), str(module_id), str(moc_id), "generate")

    os.makedirs(save_path, exist_ok=True)

    print(f"  [DEBUG] generate_lua_script: moc_name={moc_name}, script_oper={script_oper}, is_generate_code={is_generate_code}")

    result = _run_cli(
        context,
        ["moc", "generate-script", str(task_id), str(moc_id), moc_name, script_oper, str(is_generate_code)],
        step_name="generate_lua_script",
        timeout=120
    )

    # omres-cli generate-script会将二进制文件保存到临时文件，result中包含file路径
    download_file = result.get("file")
    filename = f"{moc_name}.lua"
    save_file = os.path.join(save_path, filename)

    if download_file and os.path.exists(download_file):
        shutil.move(download_file, save_file)
        print(f"  ✓ Lua脚本已保存到: {save_file}")
    else:
        # 如果没有file字段，尝试从result中提取内容写入
        content = result.get("data") or result.get("content")
        if content:
            with open(save_file, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  ✓ Lua脚本已保存到: {save_file}")
        else:
            print(f"  ⚠ 未找到下载文件路径: {result}")

    return save_file


def generate_lua_scripts_batch(
    context: WorkflowContext,
    moc_name: str,
    script_operations: List[str]
) -> dict:
    """
    批量生成Lua脚本（一次API调用生成多个操作的脚本）
    注意：只处理非LST的操作，LST操作不参与Lua脚本生成

    Args:
        context: 工作流上下文
        moc_name: MOC对象名称
        script_operations: 操作类型列表（如["SET", "LST", "ADD", "RMV"]）

    Returns:
        dict: 生成结果
    """
    non_lst_operations = [op for op in script_operations if op.upper() != "LST"]

    if not non_lst_operations:
        print(f"  ⚠ 没有需要生成Lua脚本的操作（非LST）")
        return {"status": True, "operations": [], "file": None}

    script_oper_map = {
        "SET": "SET",
        "ADD": "CREATE",
        "RMV": "DELETE"
    }

    api_operations = [script_oper_map.get(op, op.upper()) for op in non_lst_operations]
    script_oper_comma_separated = ",".join(api_operations)

    print(f"  [DEBUG] 生成Lua脚本，操作类型: {script_oper_comma_separated}")

    try:
        save_file = generate_lua_script(context, moc_name, script_oper_comma_separated, is_generate_code=0)
        return {"status": True, "operations": non_lst_operations, "file": save_file}
    except Exception as e:
        print(f"  ⚠ 生成Lua脚本失败: {e}")
        return {"status": False, "operations": non_lst_operations, "error": str(e)}


def add_error_code_with_lua_association(
    context: WorkflowContext,
    moc_name: str,
    moc_desc_ch: str,
    moc_desc_en: str,
    error_codes: List[Dict[str, Any]],
    script_operations: List[str] = None,
    service_name: str = None
) -> dict:
    """
    添加错误码并关联Lua脚本的完整流程

    处理步骤：
    1. 查询infoModuleId
    2. 添加错误码
    3. 调用insertMocInfo将MOC与Lua脚本关联
    4. 调用generateScriptFile生成Lua脚本文件

    Args:
        context: 工作流上下文
        moc_name: MOC对象名称
        moc_desc_ch: MOC中文描述
        moc_desc_en: MOC英文描述
        error_codes: 错误码配置列表，每个配置包含:
            - code: 错误码名称（如ZL_58321）
            - code_num: 错误码数字（如58321）
            - descCh: 中文描述
            - descEn: 英文描述
        script_operations: Lua脚本关联的操作类型列表（如["SET", "LST"]）
        service_name: 服务名称

    Returns:
        dict: 执行结果
    """
    print()
    print("[错误码处理] 添加错误码并关联Lua脚本")
    print("-" * 40)

    if service_name:
        info_module_id = query_info_module_id(context, service_name)
    else:
        info_module_id = context.moduleId

    added_error_codes = []

    for err_cfg in error_codes:
        print(f"  添加错误码: {err_cfg.get('code')}")
        try:
            add_error_code(
                context,
                infoCodeName=err_cfg["code"],
                infoCodeChDesc=err_cfg["descCh"],
                infoCodeEnDesc=err_cfg["descEn"],
                infoModuleId=info_module_id
            )
            added_error_codes.append(err_cfg)
        except Exception as e:
            print(f"  ⚠ 添加错误码 {err_cfg.get('code')} 失败: {e}")
            raise

    print(f"  ✓ 成功添加 {len(added_error_codes)} 个错误码")

    if script_operations is None:
        script_operations = ["SET"]

    lua_file_name = f"{moc_name}.lua"
    print(f"  [1/3] 关联Lua脚本 {lua_file_name} 与操作类型 {script_operations}...")

    insert_moc_info_with_lua(
        context,
        moc_name=moc_name,
        lua_file_name=lua_file_name,
        script_operations=script_operations,
        moc_desc_ch=moc_desc_ch,
        moc_desc_en=moc_desc_en
    )
    print(f"  ✓ Lua脚本关联成功")

    print(f"  [2/3] 生成Lua脚本文件...")
    generate_result = generate_lua_scripts_batch(context, moc_name, script_operations)
    if generate_result.get("status"):
        print(f"  ✓ Lua脚本生成成功: {generate_result.get('file')}")
    else:
        print(f"  ⚠ Lua脚本生成失败: {generate_result.get('error')}")

    context.set_state("added_error_codes", added_error_codes)

    return {
        "status": "success",
        "error_codes_added": len(added_error_codes),
        "lua_association": True,
        "lua_generation": generate_result.get("status", False)
    }


def generate_business_logic_lua(
    generated_lua_path: str,
    moc_name: str,
    service_name: str,
    fields: List[Dict[str, Any]],
    error_codes: List[Dict[str, Any]] = None,
    validation_rule: str = None
) -> str:
    """
    基于API生成的Lua脚本模板，添加业务逻辑后保存到仓库

    Args:
        generated_lua_path: API生成的Lua脚本路径
        moc_name: MOC对象名称
        service_name: 服务名称（如PcfDiamLoadBalanceService）
        fields: 字段配置列表，每个配置包含:
            - name: 字段名称
            - type: 字段类型
            - isKey: 是否主键
        error_codes: 错误码配置列表
        validation_rule: 自定义校验规则（可选）

    Returns:
        str: 生成的业务逻辑Lua脚本内容
    """
    if not os.path.exists(generated_lua_path):
        print(f"  ⚠ Lua脚本模板不存在: {generated_lua_path}")
        return None

    with open(generated_lua_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    error_code_defs = ""
    validation_logic = ""

    if error_codes:
        for err_cfg in error_codes:
            code_num = err_cfg.get('code_num', 0)
            code_name = err_cfg.get('code', '')
            desc_ch = err_cfg.get('descCh', '')
            error_code_defs += f"INFOCODE_IDERR_{code_num} = {code_num} -- {desc_ch}\n"

    if validation_rule:
        validation_logic = validation_rule
    else:
        threshold_fields = ['HIGHTSTR', 'HIGHTEND', 'OVERLOADSTR', 'OVERLOADEND']
        has_threshold_validation = all(
            any(f.get('name') == tf for f in fields)
            for tf in threshold_fields
        )

        if has_threshold_validation:
            err_code = error_codes[0].get('code_num', 0) if error_codes else 0
            validation_logic = f'''    -- ----业务自行实现逻辑开始--------------------------------
    -- 校验规则: HIGHTEND <= HIGHTSTR <= OVERLOADEND <= OVERLOADSTR
    if field[_fid_{moc_name}.HIGHTEND] > field[_fid_{moc_name}.HIGHTSTR] or
       field[_fid_{moc_name}.HIGHTSTR] > field[_fid_{moc_name}.OVERLOADEND] or
       field[_fid_{moc_name}.OVERLOADEND] > field[_fid_{moc_name}.OVERLOADSTR] then
        return OutputErrorCode(data, INFOCODE_IDERR_{err_code})
    end
    -- ----业务自行实现逻辑结束--------------------------------
'''

    update_fieldlist_func = f'''
function UpdateFieldlist(msg, fieldlist)
    local condition = {{}}
    local result, buffType = vrp.ReadMsgFields(msg, _fid_{moc_name}.BUFFTYPE)
    if result ~= OK then
        return ERR
    end
    spt_AddField(condition, _fid_{moc_name}.BUFFTYPE, buffType)
    local result, handle = spt_Query(_cid_{moc_name}, condition, true)
    if result ~= OK then
        return ERR
    end
    local result, originalRecord = spt_GetNextRecord(handle)
    spt_EndQuery(handle)
    if result ~= OK then
        return ERR
    end
    field = ParseFieldlist(fieldlist)
    for _, v in pairs(originalRecord) do
        if field[v.fid] == nil then
            spt_AddField(fieldlist, v.fid, v.value)
        end
    end
    return OK
end
'''

    business_content = template_content

    if error_code_defs:
        business_content = business_content.replace(
            "--------类结构宏定义-------------------------------------------------------------",
            f"{error_code_defs}\n--------类结构宏定义-------------------------------------------------------------"
        )

    if validation_logic:
        business_content = business_content.replace(
            "------业务自行实现逻辑开始--------------------------------\n    ----------implement yourself----------------------------\n    ----------implement yourself----------------------------\n    ------业务自行实现逻辑结束--------------------------------",
            validation_logic
        )

    if update_fieldlist_func.strip():
        insert_pos = business_content.find("\n\n\n-----------------------------------------------------------------------")
        if insert_pos != -1:
            business_content = business_content[:insert_pos] + update_fieldlist_func + business_content[insert_pos:]

    business_content = business_content.replace(
        "if OK ~= ret then\n        return OutputErrorCode(data, ret)\n    end",
        ""
    )

    business_content = business_content.replace(
        "return OK",
        f'''result = UpdateFieldlist(msg, fieldlist)
    if result ~= OK then
        return ERR
    end

    field = ParseFieldlist(fieldlist){validation_logic}
    return spt_SetRecord(data, _cid_{moc_name}, fieldlist)'''
    )

    return business_content


def copy_lua_script_to_repo(
    generated_lua_path: str,
    repo_path: str,
    service_name: str,
    moc_name: str,
    fields: List[Dict[str, Any]] = None,
    error_codes: List[Dict[str, Any]] = None,
    validation_rule: str = None
) -> str:
    """
    拷贝Lua脚本到代码仓库，并添加业务逻辑

    Args:
        generated_lua_path: API生成的Lua脚本路径
        repo_path: 代码仓库路径
        service_name: 服务名称（如PcfDiamLoadBalanceService）
        moc_name: MOC对象名称
        fields: 字段配置列表
        error_codes: 错误码配置列表
        validation_rule: 自定义校验规则（可选）

    Returns:
        str: 目标文件路径
    """
    service_short = service_name.replace("Pcf", "").replace("Service", "")
    lua_dir = os.path.join(repo_path, service_name, "om", "cfg", "microservice", "code", "modules", service_short, "lua")

    if not os.path.exists(lua_dir):
        os.makedirs(lua_dir, exist_ok=True)

    target_path = os.path.join(lua_dir, f"{moc_name}.lua")

    if fields and error_codes:
        business_content = generate_business_logic_lua(
            generated_lua_path, moc_name, service_name, fields, error_codes, validation_rule
        )
        if business_content:
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(business_content)
            print(f"  ✓ Lua脚本已生成业务逻辑并保存到: {target_path}")
            return target_path

    import shutil
    shutil.copy2(generated_lua_path, target_path)
    print(f"  ✓ Lua脚本已拷贝到: {target_path}")
    return target_path


if __name__ == "__main__":
    from context import create_context
    ctx = create_context(taskName="TEST", userName="test", moduleId=7)
    print("错误码处理模块测试需要完整流程")
