"""Fifty project-authored raw requirements for Phase 4 architecture validation."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Mapping


CASE_SET_SCHEMA_VERSION = "req2web.phase4.full_architecture.raw_case_set.v1"
CASE_SET_ID = "p4-full-architecture-raw-requirements-50"
CASE_COUNT = 50
CANARY_COUNT = 5

_CASE_KEYS = (
    "case_id",
    "request_id",
    "requirement",
    "target_device",
    "task_type",
    "constraints",
)
_PREWRITTEN_B_KEYS = frozenset({"requirement_summary", "use_cases"})


class Phase4FullArchitectureCaseError(ValueError):
    """Raised when the raw-requirement inventory drifts."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity(value: object) -> dict[str, object]:
    raw = _canonical(value)
    return {
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": CASE_SET_SCHEMA_VERSION,
    }


def _case(
    number: int,
    requirement: str,
    target_device: str,
    task_type: str,
    constraints: tuple[str, ...],
) -> dict[str, object]:
    return {
        "case_id": f"p4-full-architecture-{number:02d}",
        "request_id": f"p4-full-architecture-request-{number:02d}",
        "requirement": requirement,
        "target_device": target_device,
        "task_type": task_type,
        "constraints": list(constraints),
    }


_CASES = [
    _case(1, "做一个手机端生鲜购物页面，用户能搜索蔬菜水果、按价格筛选、加入购物车并填写配送信息，下单信息有错误时要提示但保留已经填写的正确内容。", "mobile", "ecommerce", ("适配窄屏单手操作", "输入错误后允许修改并重新提交")),
    _case(2, "做一个桌面端服装商城页面，用户可以找衣服、选择尺码颜色、查看购物袋并完成结算，库存变化和表单错误都要有清楚反馈。", "desktop", "ecommerce", ("主要操作可用键盘完成", "库存不足时不得丢失购物袋内容")),
    _case(3, "做一个平板端数码产品购买页面，希望能搜索和比较商品、加入购物车、填写收货地址并提交订单，失败后可以继续修改。", "tablet", "ecommerce", ("横竖屏都能使用", "地址校验失败时保留有效字段")),
    _case(4, "做一个手机端图书商城，用户能够搜索书名、查看图书信息、加入购物车和提交购买，空结果和提交失败都要告诉用户下一步怎么办。", "mobile", "ecommerce", ("支持清除搜索条件", "失败提示必须包含恢复入口")),
    _case(5, "做一个响应式宠物用品商店，用户能筛选商品、选择规格、查看购物车并填写配送信息，缺货或输入不完整时不要清空当前操作。", "responsive_web", "ecommerce", ("适配手机和平板", "错误恢复不得清空已选商品")),
    _case(6, "做一个桌面家居商城页面，用户想按房间和价格找商品、查看详情、加入购物车并预约配送，预约时间不合法时要能修正。", "desktop", "ecommerce", ("筛选状态需要可见", "预约失败后保留购物车")),
    _case(7, "做一个手机端美妆购买页面，用户可以搜索品牌、筛选肤质、选择商品并结算，优惠码或地址错误时要显示在对应位置。", "mobile", "ecommerce", ("错误提示靠近对应输入项", "结算前展示费用明细")),
    _case(8, "做一个桌面运动装备商店，用户能筛选运动类型、比较商品、加入购物车并提交订单，支付失败时应保留订单内容供再次操作。", "desktop", "ecommerce", ("比较信息保持对齐", "支付失败不得重复创建订单")),
    _case(9, "做一个平板端套餐订购页面，用户能挑选菜品、调整人数、查看购物车并填写配送要求，超出配送范围时给出可修改方案。", "tablet", "ecommerce", ("人数变化同步更新价格", "配送失败提供修改地址入口")),
    _case(10, "做一个手机办公用品采购页面，用户搜索用品、选择数量、加入采购清单并提交审批，数量或预算不合法时保留其他有效项目。", "mobile", "ecommerce", ("数量修改即时更新总额", "预算错误不清空采购清单")),
    _case(11, "做一个手机宠物情绪识别页面，用户拍照或上传图片后查看分析结果，图片不可用或识别失败时能重新选择。", "mobile", "recognition_tool", ("先说明相机和图片权限用途", "失败后保留重新上传入口")),
    _case(12, "做一个网页植物识别工具，用户上传叶片照片后查看可能的植物和养护建议，图片格式错误时给出清楚提示。", "web", "recognition_tool", ("不把识别结果表述为绝对结论", "支持重新上传图片")),
    _case(13, "做一个手机票据识别页面，用户上传收据后查看提取的商家、日期和金额并确认，识别错误时可以逐项修改。", "mobile", "recognition_tool", ("原图和提取字段可对照", "修改后需要再次确认")),
    _case(14, "做一个桌面文档分类工具，用户拖入文件后查看分类和处理状态，文件不支持或分析失败时可以移除并重新提交。", "desktop", "recognition_tool", ("展示上传和分析进度", "失败文件不得影响其他文件")),
    _case(15, "做一个平板食材识别页面，用户拍摄冰箱里的食材后查看识别列表并修正数量，识别不确定时要明确标出。", "tablet", "recognition_tool", ("低置信结果需要单独提示", "用户可以删除错误项目")),
    _case(16, "做一个网页图片质量检查工具，用户上传图片后查看模糊、曝光和尺寸问题，上传失败时能重新选择而不刷新整个页面。", "web", "recognition_tool", ("结果按问题类型分组", "禁止自动上传未确认文件")),
    _case(17, "做一个手机语音转文字页面，用户录音或选择音频后查看转写结果并编辑，权限拒绝或文件过长时提供恢复办法。", "mobile", "recognition_tool", ("明确麦克风权限状态", "转写文本允许编辑保存")),
    _case(18, "做一个桌面日志分析页面，用户粘贴一段日志后查看错误类别和关键位置，输入为空或格式异常时给出示例。", "desktop", "recognition_tool", ("不上传日志到外部服务", "错误位置需要可复制")),
    _case(19, "做一个手机附近服务查找页面，用户输入地点或使用定位后查看附近门店、筛选服务并选择一个地点，定位被拒绝时允许手动输入。", "mobile", "location_service", ("定位权限拒绝后提供手动地址", "筛选条件保持可见")),
    _case(20, "做一个桌面配送范围查询页面，用户输入地址后查看是否可配送、预计时间和可选时段，地址找不到时能够修改。", "desktop", "location_service", ("不得在地址无效时显示成功", "保留用户已输入地址")),
    _case(21, "做一个平板停车位查找页面，用户选择区域和时间后查看可用停车场并预选一个位置，时间冲突时显示其他选择。", "tablet", "location_service", ("地图和列表状态同步", "冲突后保留区域选择")),
    _case(22, "做一个网页活动地点选择页面，用户搜索场地、按容量筛选并查看详情，找不到结果时可以清除部分条件。", "web", "location_service", ("空结果提供清除筛选入口", "容量条件不得静默丢失")),
    _case(23, "做一个手机旅行路线页面，用户输入起点终点后查看路线步骤并选择偏好，定位不可用时仍可手动完成。", "mobile", "location_service", ("路线切换保留起终点", "定位失败不阻止手动输入")),
    _case(24, "做一个桌面服务区域管理页面，工作人员搜索地址、查看所属区域并提交调整建议，地址歧义时要求确认候选地址。", "desktop", "location_service", ("歧义地址必须人工确认", "提交前展示变更摘要")),
    _case(25, "做一个响应式新闻阅读页面，用户浏览文章、按主题筛选、保存文章并查看收藏，加载失败时可重新尝试。", "responsive_web", "content_platform", ("保留当前筛选和滚动位置", "收藏状态需要即时反馈")),
    _case(26, "做一个手机课程内容页面，学生搜索课程、查看章节、标记完成并继续学习，网络失败时要显示尚未同步的状态。", "mobile", "content_platform", ("完成状态必须可追踪", "同步失败不得误报成功")),
    _case(27, "做一个桌面视频资料库，用户搜索视频、筛选时长和主题、查看详情并加入稍后观看，空结果时能调整筛选。", "desktop", "content_platform", ("键盘可操作主要控件", "空结果显示已启用条件")),
    _case(28, "做一个平板食谱浏览页面，用户按食材搜索、查看步骤、收藏食谱并生成购物清单，缺少食材时可以替换。", "tablet", "content_platform", ("步骤适合厨房场景阅读", "替换食材后更新购物清单")),
    _case(29, "做一个网页知识库页面，用户搜索问题、查看分类结果、打开答案并反馈是否有帮助，搜索失败时保留查询内容。", "web", "content_platform", ("答案来源信息可见", "反馈提交失败允许重试")),
    _case(30, "做一个手机播客发现页面，用户搜索节目、按主题筛选、查看单集并加入播放列表，加载失败时不丢失列表。", "mobile", "content_platform", ("播放列表修改需要反馈", "失败后保留当前节目")),
    _case(31, "做一个手机团队消息页面，用户查看会话、搜索消息、发送内容并处理发送失败，失败消息可以再次发送。", "mobile", "social_communication", ("发送状态必须可见", "失败消息不得重复显示为成功")),
    _case(32, "做一个桌面评论管理页面，管理员筛选评论、查看详情、标记处理并提交回复，操作失败时保留未发送内容。", "desktop", "social_communication", ("敏感操作需要确认", "回复失败保留草稿")),
    _case(33, "做一个平板社区讨论页面，用户浏览话题、按标签筛选、发布评论并查看结果，内容为空或提交失败时能修正。", "tablet", "social_communication", ("空评论不得提交", "提交失败保持编辑状态")),
    _case(34, "做一个网页客服会话页面，客服搜索用户、查看历史消息、发送回复并标记会话状态，网络异常时显示待发送。", "web", "social_communication", ("历史消息和当前输入分区清楚", "待发送状态可恢复")),
    _case(35, "做一个手机活动群组页面，用户查看成员、搜索公告、报名活动并取消报名，名额变化时给出准确反馈。", "mobile", "social_communication", ("报名状态不能重复提交", "名额不足给出返回入口")),
    _case(36, "做一个桌面销售数据仪表盘，用户选择日期和区域后查看关键指标、图表和异常列表，查询失败时保留筛选条件。", "desktop", "dashboard", ("图表同时提供文字摘要", "筛选变化需要刷新状态")),
    _case(37, "做一个平板设备监控页面，值班人员查看设备状态、筛选异常、打开详情并确认处理，数据过期时要明确提示。", "tablet", "dashboard", ("过期数据不得显示为实时", "确认操作需要记录反馈")),
    _case(38, "做一个网页项目进度面板，用户查看任务概览、按负责人筛选、打开延期任务并更新状态，保存失败时保留修改。", "web", "dashboard", ("延期状态需要醒目标识", "保存失败不得丢失输入")),
    _case(39, "做一个桌面库存管理页面，工作人员搜索商品、筛选低库存、查看变化并提交补货数量，数量错误时就地修改。", "desktop", "dashboard", ("库存数字格式统一", "无效数量不得提交")),
    _case(40, "做一个手机个人支出概览页面，用户选择月份、查看分类统计、打开明细并记录一笔支出，金额错误时能修正。", "mobile", "dashboard", ("金额输入需要格式检查", "图表颜色之外还需文字标签")),
    _case(41, "做一个手机门诊预约页面，用户选择科室和日期、查看可约时间、填写信息并提交，时间被占用时提供其他选择。", "mobile", "web_application", ("不展示真实患者数据", "预约失败保留已填信息")),
    _case(42, "做一个桌面会议室预约页面，员工筛选容量和设备、选择时间并提交预约，发生冲突时展示可选时间。", "desktop", "web_application", ("冲突信息必须明确", "修改时间不清空其他条件")),
    _case(43, "做一个平板维修报修页面，用户选择设备、描述问题、上传图片并提交，缺少必填内容时定位到对应字段。", "tablet", "web_application", ("上传图片前显示预览", "错误提示靠近字段")),
    _case(44, "做一个网页课程报名页面，用户搜索课程、查看名额、填写资料并提交报名，名额不足或表单错误时允许修改。", "web", "web_application", ("不得超额报名", "错误后保留有效字段")),
    _case(45, "做一个手机外卖评价页面，用户查看订单、选择评分、填写评价并提交，网络失败时保存未提交内容。", "mobile", "web_application", ("评分和文字评价可分别修改", "失败时保留草稿")),
    _case(46, "做一个桌面费用报销页面，员工填写费用、添加项目、上传凭证并提交审批，金额不平或文件错误时逐项提示。", "desktop", "web_application", ("总额自动计算", "错误项目不影响其他项目")),
    _case(47, "做一个平板酒店入住登记页面，工作人员查找预订、确认客人信息、分配房间并提交，信息不一致时要求确认。", "tablet", "web_application", ("隐私字段默认遮挡", "提交前展示确认摘要")),
    _case(48, "做一个网页反馈收集页面，用户选择问题类型、描述情况、添加联系方式并提交，内容不足时提示需要补充什么。", "web", "web_application", ("联系方式为可选字段", "成功后给出反馈编号")),
    _case(49, "做一个手机志愿活动报名页面，用户筛选活动、查看要求、填写报名信息并提交，资格条件不满足时说明原因。", "mobile", "web_application", ("资格失败不能显示报名成功", "返回后保留筛选")),
    _case(50, "做一个桌面文件申请页面，用户选择申请类型、填写用途、添加附件并提交，附件或字段有问题时可以修改后再次提交。", "desktop", "web_application", ("附件状态清楚可见", "重复提交需要阻止")),
]


def validate_raw_case(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or tuple(value) != _CASE_KEYS:
        raise Phase4FullArchitectureCaseError("raw case exact keys drifted")
    if _PREWRITTEN_B_KEYS & set(value):
        raise Phase4FullArchitectureCaseError("raw case contains prewritten B fields")
    row = copy.deepcopy(dict(value))
    for key in ("case_id", "request_id", "requirement", "target_device", "task_type"):
        if (
            not isinstance(row[key], str)
            or not row[key]
            or row[key] != row[key].strip()
        ):
            raise Phase4FullArchitectureCaseError(f"raw case {key} is invalid")
    if len(str(row["requirement"])) < 30:
        raise Phase4FullArchitectureCaseError("raw requirement is too short")
    constraints = row["constraints"]
    if (
        not isinstance(constraints, list)
        or not constraints
        or any(
            not isinstance(item, str) or not item or item != item.strip()
            for item in constraints
        )
        or len(constraints) != len(set(constraints))
    ):
        raise Phase4FullArchitectureCaseError("raw case constraints are invalid")
    return row


def get_raw_case_set() -> dict[str, object]:
    cases = [validate_raw_case(item) for item in _CASES]
    if len(cases) != CASE_COUNT:
        raise Phase4FullArchitectureCaseError("raw case count drifted")
    if len({str(item["case_id"]) for item in cases}) != CASE_COUNT:
        raise Phase4FullArchitectureCaseError("raw case IDs are duplicated")
    if len({str(item["request_id"]) for item in cases}) != CASE_COUNT:
        raise Phase4FullArchitectureCaseError("raw request IDs are duplicated")
    if len({str(item["requirement"]) for item in cases}) != CASE_COUNT:
        raise Phase4FullArchitectureCaseError("raw requirements are duplicated")
    root = {
        "schema_version": CASE_SET_SCHEMA_VERSION,
        "case_set_id": CASE_SET_ID,
        "case_count": CASE_COUNT,
        "canary_count": CANARY_COUNT,
        "case_order": [item["case_id"] for item in cases],
        "prewritten_b_fields_present": False,
        "cases": cases,
    }
    return {**root, "case_set_identity": _identity(root)}


__all__ = [
    "CANARY_COUNT",
    "CASE_COUNT",
    "CASE_SET_ID",
    "CASE_SET_SCHEMA_VERSION",
    "Phase4FullArchitectureCaseError",
    "get_raw_case_set",
    "validate_raw_case",
]
