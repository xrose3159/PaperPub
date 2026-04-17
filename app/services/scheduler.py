"""调度器 — 每日爬虫（APScheduler）+ 自主 Agent 循环（AgentRunner）。

流程：每日爬虫完成后自动触发推荐 + AI 总结，保证总结基于当天最新论文。
"""

from __future__ import annotations

import logging
import os
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.agent_loop import AgentRunner
from app.services.arxiv_crawler import crawl
from app.services.recommender import generate_daily_recommendations, generate_daily_summaries

AGENT_LOOP_ENABLED = os.getenv("AGENT_LOOP_ENABLED", "false").lower() in ("true", "1", "yes")

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
agent_runner = AgentRunner()


def job_daily_pipeline():
    """每日全流程：爬取论文 → 生成推荐 → 生成 AI 总结。"""

    # ── 1. 爬取论文 ──
    logger.info("⏰ [定时任务] 开始每日爬虫...")
    t0 = time.time()
    try:
        count = crawl(category="cs.*", max_results=100, days_back=5)
        elapsed = time.time() - t0
        minutes, seconds = divmod(elapsed, 60)
        logger.info(
            "⏰ [定时任务] 每日爬虫完成，新增 %d 篇论文，总耗时 %d 分 %.1f 秒",
            count, int(minutes), seconds,
        )
    except Exception:
        elapsed = time.time() - t0
        logger.exception("⏰ [定时任务] 每日爬虫异常（耗时 %.1f 秒），继续执行推荐与总结", elapsed)

    # ── 2. 生成推荐 ──
    logger.info("📬 [定时任务] 开始每日论文推荐...")
    t1 = time.time()
    try:
        rec_count = generate_daily_recommendations()
        elapsed = time.time() - t1
        logger.info("📬 [定时任务] 推荐完成，共为 %d 位用户生成推荐（%.1f 秒）", rec_count, elapsed)
    except Exception:
        logger.exception("📬 [定时任务] 推荐生成异常")

    # ── 3. 生成 AI 总结 ──
    logger.info("📝 [定时任务] 开始生成每日 AI 总结...")
    t2 = time.time()
    try:
        summary_count = generate_daily_summaries()
        elapsed = time.time() - t2
        logger.info("📝 [定时任务] AI 总结完成，共为 %d 位用户生成总结（%.1f 秒）", summary_count, elapsed)
    except Exception:
        logger.exception("📝 [定时任务] AI 总结生成异常")

    total = time.time() - t0
    minutes, seconds = divmod(total, 60)
    logger.info("✅ [定时任务] 每日全流程完成，总耗时 %d 分 %.1f 秒", int(minutes), seconds)


# ── 生命周期 ────────────────────────────────────────────────

async def start_scheduler():
    """启动每日定时任务 + 自主 Agent 循环。"""
    scheduler.add_job(
        job_daily_pipeline,
        trigger=CronTrigger(hour=23, minute=24),
        id="daily_pipeline_2324",
        name="每日23:24 爬取论文→推荐→AI总结",
        replace_existing=True,
    )
    scheduler.start()

    jobs = scheduler.get_jobs()
    logger.info("📅 定时任务已启动，共 %d 个:", len(jobs))
    for j in jobs:
        logger.info("   - %s | 下次执行: %s", j.name, j.next_run_time)

    if AGENT_LOOP_ENABLED:
        await agent_runner.start()
    else:
        logger.info("🤖 Agent 自主循环已禁用 (AGENT_LOOP_ENABLED=false)")


async def stop_scheduler():
    """关闭定时任务和 Agent 循环。"""
    await agent_runner.stop()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("📅 调度器已关闭")
