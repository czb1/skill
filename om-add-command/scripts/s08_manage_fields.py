"""
[Skill-08/09/10] 字段管理：添加字段、查询字段、设置字段类型
通过 omres-cli moc-field add-name / select-name / update-info 和 default-record add 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING, Dict, Any, List
from context import WorkflowContext, StepExecutionError
from omres_cli import (
    find_omres_cli as _find_omres_cli,
    run_cli as _run_cli,
    DEFAULT_TIMEOUT as _DEFAULT_TIMEOUT,
    is_duplicate_error as _is_duplicate_error,
)

if TYPE_CHECKING:
    from typing import Optional


def add_field(
    context: WorkflowContext,
    fieldName: str,
    isKey: int = 0,
    isMandatory: int = 0,
    mocId: int = None,
    reuse_if_exists: bool = True
) -> dict:
    """
    添加字段到原子对象 — 通过omres-cli moc-field add-name；同名字段已存在时复用

    复用已有原子对象（建模文件里已有同名对象）时，部分字段可能已经存在，
    此时不应该整个流程失败：字段本身复用，后面的 update_field_info 会把
    类型/范围/默认值按本次配置刷新一遍。

    Args:
        context: 工作流上下文
        fieldName: 字段名称
        isKey: 是否主键 (0=否, 1=是)
        isMandatory: 是否必填 (0=否, 1=是)
        mocId: 原子对象ID
        reuse_if_exists: 同名字段已存在时复用而不是报错（默认True）

    Returns:
        dict: 添加结果；复用已有字段时额外带 reused=True
    """
    mocId = mocId or context.get_required_state("mocId")
    task_id = context.get_required_state("taskId")

    if not fieldName:
        raise StepExecutionError(
            step_name="add_field",
            message="字段名称不能为空",
            context_state=context.state
        )

    body = json.dumps({
        "fieldName": fieldName,
        "isKey": str(isKey),
        "isCustomKey": "0",
        "isUnique": "0",
        "isMandatory": str(isMandatory),
        "m2v": "1",
        "taskId": str(task_id),
        "mocId": mocId
    }, ensure_ascii=False)

    print(f"  [DEBUG] add_field: fieldName={fieldName}, mocId={mocId}, taskId={task_id}")

    try:
        result = _run_cli(
            context,
            ["moc-field", "add-name", "--body", body],
            step_name="add_field",
            timeout=_DEFAULT_TIMEOUT
        )
    except StepExecutionError as e:
        if not (reuse_if_exists and _is_duplicate_error(e.message)):
            raise
        # 确认确实是已存在的字段（而不是文案凑巧命中），再放行
        if not field_exists(context, fieldName, mocId):
            raise
        print(f"  [INFO] 字段 {fieldName} 已存在于 mocId={mocId}，复用并按本次配置更新类型")
        context.set_state(f"field_added_{fieldName}", True)
        context.set_state(f"field_reused_{fieldName}", True)
        return {"status": True, "reused": True, "message": f"字段{fieldName}已存在，复用"}

    context.set_state(f"field_added_{fieldName}", True)

    return result


def field_exists(context: WorkflowContext, fieldName: str, mocId: int = None) -> bool:
    """
    判断原子对象上是否已有同名字段（实时查询，不依赖缓存的field_map）

    Args:
        context: 工作流上下文
        fieldName: 字段名称
        mocId: 原子对象ID

    Returns:
        bool: 已存在返回True
    """
    try:
        result = query_field_list(context, mocId=mocId)
    except StepExecutionError as e:
        print(f"  [WARNING] 查询字段列表失败，无法判断 {fieldName} 是否已存在: {e.message}")
        return False

    for field in result.get("data", []) or []:
        if field.get("fieldName") == fieldName:
            return True
    return False


def query_field_list(context: WorkflowContext, mocId: int = None, moduleId: int = None) -> dict:
    """
    查询字段列表 — 通过omres-cli moc-field select-name

    Args:
        context: 工作流上下文
        mocId: 原子对象ID
        moduleId: 模块ID

    Returns:
        dict: 包含字段列表的响应
    """
    mocId = mocId or context.get_required_state("mocId")
    moduleId = moduleId or context.moduleId
    task_id = context.get_required_state("taskId")

    body = json.dumps({
        "taskId": str(task_id),
        "mocId": mocId,
        "moduleId": moduleId
    }, ensure_ascii=False)

    print(f"  [DEBUG] query_field_list: taskId={task_id}, mocId={mocId}, moduleId={moduleId}")

    result = _run_cli(
        context,
        ["moc-field", "select-name", "--body", body],
        step_name="query_field_list",
        timeout=_DEFAULT_TIMEOUT
    )

    # 提取fieldId映射
    field_map = {}
    if isinstance(result, dict) and result.get("data"):
        for field in result.get("data", []):
            fname = field.get("fieldName")
            fid = field.get("fieldId")
            if fname and fid:
                field_map[fname] = fid

    context.set_state("field_map", field_map)
    context.set_state("field_list", result.get("data", []))

    return result


def update_field_info(
    context: WorkflowContext,
    fieldName: str,
    dataTypeId: int,
    dataTypeName: str,
    rangeStr: str = None,
    fieldDescCh: str = None,
    fieldDescEn: str = None,
    isKey: int = 0,
    isMandatory: int = 0,
    defaultValue: str = "",
    invalidValue: str = "",
    customizeDataTypeId: int = None,
    mocId: int = None,
    fieldId: int = None
) -> dict:
    """
    更新字段信息(设置数据类型) — 通过omres-cli moc-field update-info

    Args:
        context: 工作流上下文
        fieldName: 字段名称
        dataTypeId: 数据类型ID (8=uint32, 25=ENUM等)
        dataTypeName: 数据类型名称
        rangeStr: 取值范围
        fieldDescCh: 中文描述
        fieldDescEn: 英文描述
        isKey: 是否主键
        isMandatory: 是否必填
        defaultValue: 默认值
        invalidValue: 无效值
        customizeDataTypeId: 自定义类型ID (枚举类型用)
        mocId: 原子对象ID
        fieldId: 字段ID

    Returns:
        dict: 更新结果
    """
    mocId = mocId or context.get_required_state("mocId")
    moduleId = context.moduleId
    task_id = context.get_required_state("taskId")

    # 如果没有提供fieldId，从field_map中查找
    if not fieldId:
        field_map = context.get_state("field_map", {})
        fieldId = field_map.get(fieldName)

    if not fieldId:
        raise StepExecutionError(
            step_name="update_field_info",
            message=f"未找到字段ID: {fieldName}",
            context_state=context.state
        )

    body = json.dumps({
        "taskId": task_id,
        "mocId": mocId,
        "fieldId": fieldId,
        "fieldName": fieldName,
        "fieldDescCh": fieldDescCh or fieldName,
        "fieldDescEN": fieldDescEn or fieldName,
        "isKey": isKey,
        "isCustomKey": 0,
        "isUnique": 0,
        "isMandatory": isMandatory,
        "isIndexField": "否",
        "dataTypeId": dataTypeId,
        "dataTypeName": dataTypeName,
        "range": rangeStr or "",
        "defaultValue": defaultValue,
        "invalidValue": invalidValue,
        "m2v": 1,
        "customizeDataTypeId": customizeDataTypeId or -1,
        "isSupportFuzzyQuery": 0
    }, ensure_ascii=False)

    print(f"  [DEBUG] update_field_info: fieldName={fieldName}, fieldId={fieldId}, dataTypeId={dataTypeId}")

    result = _run_cli(
        context,
        ["moc-field", "update-info", "--body", body],
        step_name="update_field_info",
        timeout=_DEFAULT_TIMEOUT
    )

    context.set_state(f"field_type_set_{fieldName}", True)

    return result


def add_default_record(context: WorkflowContext, defaultRecords: Dict[str, str], mocId: int = None) -> dict:
    """
    添加默认值记录（单行） — 通过omres-cli default-record add

    Args:
        context: 工作流上下文
        defaultRecords: 默认值字典 {"fieldId": "defaultValue", ...}
        mocId: 原子对象ID

    Returns:
        dict: 添加结果
    """
    mocId = mocId or context.get_required_state("mocId")
    task_id = context.get_required_state("taskId")

    body = json.dumps({
        "taskId": task_id,
        "mocId": mocId,
        "defaultRecords": defaultRecords
    }, ensure_ascii=False)

    print(f"  [DEBUG] add_default_record: mocId={mocId}, taskId={task_id}")

    result = _run_cli(
        context,
        ["default-record", "add", "--body", body],
        step_name="add_default_record",
        timeout=_DEFAULT_TIMEOUT
    )

    context.set_state("default_record_added", True)

    return result


def add_default_records(context: WorkflowContext, defaultRecordsList: List[Dict[str, str]], mocId: int = None) -> dict:
    """
    添加多行默认值记录 — 通过omres-cli default-record add

    Args:
        context: 工作流上下文
        defaultRecordsList: 默认值字典列表，每行记录一个字典
        mocId: 原子对象ID

    Returns:
        dict: 最后一行的添加结果
    """
    mocId = mocId or context.get_required_state("mocId")
    task_id = context.get_required_state("taskId")

    last_result = None
    for idx, default_record in enumerate(defaultRecordsList):
        body = json.dumps({
            "taskId": task_id,
            "mocId": mocId,
            "defaultRecords": default_record
        }, ensure_ascii=False)

        print(f"  [DEBUG] add_default_records第{idx+1}行: mocId={mocId}, taskId={task_id}")

        try:
            last_result = _run_cli(
                context,
                ["default-record", "add", "--body", body],
                step_name="add_default_records",
                timeout=_DEFAULT_TIMEOUT
            )
        except StepExecutionError as e:
            print(f"  [WARNING] 添加第{idx+1}行默认值记录失败: {e.message}")
            last_result = {"status": False, "message": e.message}
            continue

        print(f"  ✓ 第{idx+1}行默认值记录添加成功")

    context.set_state("default_record_added", True)

    return last_result


if __name__ == "__main__":
    from context import create_context
    ctx = create_context(taskName="TEST", userName="test", moduleId=5)
    print("字段管理模块测试需要完整流程")
