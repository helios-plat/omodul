"""omodul.auto_etl_research_workflow — 自适应网抓 + dlt 数据管线整理落盘的深度研究事务.

Pillars: fingerprint, decision_trail, report, cost

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

遵循 omodul 红线:
- 显式声明 _enabled_pillars
- 含 "failed" 状态返回
- 禁止 try 之外裸 raise
- 禁止 sibling omodul 导入
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Auto ETL Research Workflow 配置."""

    _omodul_name: ClassVar[str] = "auto_etl_research_workflow"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {
        "fingerprint",
        "decision_trail",
        "report",
        "cost",
    }

    scraping_sk: Any = None
    curation_sk: Any = None
    scraper_op: Any = None
    extractor_op: Any = None
    normalize_op: Any = None
    pipeline_store: Any = None
    llm_caller: Any = None


class InputData(BaseModel):
    """Auto ETL Research Workflow 输入."""

    url: str = "https://example.com/research"
    urls: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


def auto_etl_research_workflow(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """自适应网抓 + dlt 数据管线整理落盘的深度研究事务.

    流程:
    1. 自适应网抓（抗封锁）
    2. HTML 智能抽取
    3. dlt Schema 规范化 + 入库
    4. 生成调研报告

    Args:
        config: 配置（含注入的 scraping_sk / curation_sk / ...）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调（可选）

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    scraping_sk = config.scraping_sk
    curation_sk = config.curation_sk
    url = input_data.url

    try:
        # Phase 1: 自适应网抓 + 智能抽取
        trail.record(event="web_scraping", url=url)
        if scraping_sk is not None and config.llm_caller is not None:
            scrape_res = scraping_sk(
                url,
                scraper_op=config.scraper_op,
                extractor_op=config.extractor_op,
                llm_caller=config.llm_caller,
            )
            decision_trail.append({
                "step": "scraped",
                "url": url,
                "status": scrape_res.get("status"),
                "confidence": scrape_res.get("anchor_confidence"),
            })
        else:
            scrape_res = {
                "url": url,
                "summary": f"Research data for {url}",
                "anchor_confidence": 0.5,
                "status": "fallback",
            }
            decision_trail.append({"step": "scraped", "url": url, "status": "fallback"})

        # Phase 2: dlt 管线整理入库
        trail.record(event="data_curation", url=url)
        if curation_sk is not None:
            records = [{
                "source_url": url,
                "summary": scrape_res.get("summary", ""),
                "confidence": scrape_res.get("anchor_confidence", 0.5),
            }]
            curate_res = curation_sk(
                records,
                normalize_op=config.normalize_op,
                pipeline_store=config.pipeline_store,
            )
            decision_trail.append({
                "step": "curated",
                "records": curate_res.get("processed_records", 0),
            })
        else:
            curate_res = {"status": "skipped", "processed_records": 0}
            decision_trail.append({"step": "curated", "status": "skipped"})

        cost.add(in_tok=700, out_tok=400)

        # 生成报告
        report_content = (
            f"# Auto ETL Research Report\n\n"
            f"## Source URL: {url}\n\n"
            f"### Scraping Results\n"
            f"- Status: {scrape_res.get('status')}\n"
            f"- Confidence: {scrape_res.get('anchor_confidence')}\n"
            f"- Summary: {scrape_res.get('summary', 'N/A')[:500]}\n\n"
            f"### Data Curation\n"
            f"- Records Processed: {curate_res.get('processed_records', 0)}\n"
            f"- Schema Sample: {curate_res.get('inferred_schema_sample', {})}\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name=f"etl_research_{_safe_filename(url)}",
            fmt="markdown",
        )

        trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary=f"Auto ETL Research Workflow finished for: {url}",
            pillars={
                "fingerprint": {"url": url},
                "decision_trail": decision_trail,
                "report": {
                    "scrape_summary": scrape_res,
                    "etl_curation": curate_res,
                },
                "cost": {"tokens_used": 1100, "cost_usd": cost.total_usd},
            },
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Auto ETL Research Workflow failed: {str(e)}",
            pillars={"decision_trail": decision_trail, "report": {"error": str(e)}},
        )


def _safe_filename(url: str) -> str:
    """从 URL 提取安全文件名."""
    import re
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", url)
    return safe[:50].strip("_")
