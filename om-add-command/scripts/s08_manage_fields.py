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
    iter_data_records as _iter_data_records,
    pick_value as _pick_value,
)

if TYPE_CHECKING:
    from typing import Optional


# 后端不同接口对「字段」的叫法不统一（字段/属性/attr），键名都兜住
_FIELD_NAME_KEYS = ("fieldName", "attrName", "attributeName", "fieldEnName", "name")
_FIELD_ID_KEYS = ("fieldId", "attrId", "attributeId", "id")


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

        # 后端说已存在（如「属性名称已存在」），必须能查到它的 fieldId 才能继续，
        # 因为后面设置类型、加命令参数、加默认值都要用这个 id
        if field_exists(context, fieldName, mocId):
            print(f"  [INFO] 字段 {fieldName} 已存在于 mocId={mocId}，复用并按本次配置更新类型")
            context.set_state(f"field_added_{fieldName}", True)
            context.set_state(f"field_reused_{fieldName}", True)
            return {"status": True, "reused": True, "message": f"字段{fieldName}已存在，复用"}

        # 查不到就别硬撑：继续走下去只会在 update_field_info 抛「未找到字段ID」，
        # 反而看不出真正的原因。这里把两边的事实一起报出来
        known = sorted(context.get_state("field_map", {}).keys())
        raise StepExecutionError(
            step_name="add_field",
            message=(
                f"添加字段 {fieldName} 时后端报「{e.message}」，但在 mocId={mocId} 上查不到该字段"
                f"（moc-field select-name 查到 {len(known)} 个字段: {known}）。"
                f"可能是该名称在模块级别已被占用，或字段挂在别的原子对象上；"
                f"请确认 mocId/moduleId 是否指向需求要求的那个对象"
            ),
            context_state=context.state
        )

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
        query_field_list(context, mocId=mocId)
    except StepExecutionError as e:
        print(f"  [WARNING] 查询字段列表失败，无法判断 {fieldName} 是否已存在: {e.message}")
        return False

    return fieldName in context.get_state("field_map", {})


def _extract_field_map(result) -> dict:
    """从查询结果里提取 {字段名: fieldId}，兼容不同的包装形状与键名别名"""
    field_map = {}
    for field in _iter_data_records(result):
        fname = _pick_value(field, _FIELD_NAME_KEYS)
        fid = _pick_value(field, _FIELD_ID_KEYS)
        if fname and fid is not None:
            field_map[fname] = fid
    return field_map


def query_field_list(context: WorkflowContext, mocId: int = None, moduleId: int = None) -> dict:
    """
    查询字段列表 — 通过omres-cli moc-field select-name

    解析建模文件导入的对象，其字段同样从这里查。查不到会导致「字段已存在」
    被误判成「不存在」，所以这里做了两件事：键名/形状兼容，以及查空时
    去掉 moduleId 再试一次并把原始响应打出来便于定位。

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

    def _query(with_module: bool) -> dict:
        payload = {"taskId": str(task_id), "mocId": mocId}
        if with_module:
            payload["moduleId"] = moduleId
        return _run_cli(
            context,
            ["moc-field", "select-name", "--body", json.dumps(payload, ensure_ascii=False)],
            step_name="query_field_list",
            timeout=_DEFAULT_TIMEOUT
        )

    print(f"  [DEBUG] query_field_list: taskId={task_id}, mocId={mocId}, moduleId={moduleId}")

    result = _query(with_module=True)
    field_map = _extract_field_map(result)

    # 带 moduleId 查不到时，去掉 moduleId 再试一次（部分接口对导入对象不认这个过滤条件）
    if not field_map:
        try:
            retry_result = _query(with_module=False)
        except StepExecutionError as e:
            print(f"  [DEBUG] query_field_list 不带moduleId重试失败: {e.message}")
            retry_result = None

        retry_map = _extract_field_map(retry_result) if retry_result else {}
        if retry_map:
            print(f"  [INFO] query_field_list 带moduleId查不到字段，不带moduleId查到 {len(retry_map)} 个")
            result, field_map = retry_result, retry_map
        else:
            # 两种查法都查不到：把原始响应打出来，避免后面「字段已存在但查不到」时无从定位
            print(f"  [DEBUG] query_field_list 未解析出任何字段，原始响应: {str(result)[:500]}")

    context.set_state("field_map", field_map)
    context.set_state("field_list", _iter_data_records(result))

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
