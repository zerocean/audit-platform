"""Pydantic 请求/响应模型"""
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from enum import Enum


# ═══════════════════════════════════════════════════════════
# Audit
# ═══════════════════════════════════════════════════════════

class AuditJSONRequest(BaseModel):
    data: Dict[str, Any]  # Vision Parser 输出的 JSON
    model: Optional[str] = "dashscope-api"
    filename: Optional[str] = "structured-data.json"


class AuditPDFResponse(BaseModel):
    success: bool
    parsed_json: Optional[Dict[str, Any]] = None
    pages: int = 0
    parser_success: bool = False
    error: Optional[str] = None
    oss_url: Optional[str] = None  # OSS 上的 JSON 地址


class InspectorResponse(BaseModel):
    success: bool
    total_pages: int = 0
    total_issues: int = 0
    issues: List[str] = []
    model: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# TaxFill
# ═══════════════════════════════════════════════════════════

class TaxFillParseResponse(BaseModel):
    success: bool
    run_id: Optional[str] = None
    fs_pages: int = 0
    taxcomp_pages: int = 0
    fs_parsed_oss_url: Optional[str] = None
    taxcomp_parsed_oss_url: Optional[str] = None
    error: Optional[str] = None


class TaxFillPipelineResponse(BaseModel):
    success: bool
    run_id: Optional[str] = None
    filling_json: Optional[Dict[str, Any]] = None
    excel_oss_url: Optional[str] = None
    json_oss_url: Optional[str] = None
    ton_oss_url: Optional[str] = None
    error: Optional[str] = None
    total_tokens: int = 0
    total_cost: float = 0.0


# ═══════════════════════════════════════════════════════════
# Admin Reporter
# ═══════════════════════════════════════════════════════════

class StepUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model_name: Optional[str] = None
    model_provider: Optional[str] = None
    duration_ms: Optional[int] = None


class StepReport(BaseModel):
    task_id: str
    step_type: str
    status: str  # success | failed
    usage: StepUsage
    files: List[Dict[str, str]] = []


class TaskCreate(BaseModel):
    project_name: str  # 'audit-report-review' | 'TaxFill_HK'
    source: str = "frontend"
    input_filename: str
    input_file_count: int = 1
    input_file_size: Optional[int] = None
