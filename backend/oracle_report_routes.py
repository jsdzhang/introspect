import os
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import select
from db_models import (
    OracleReports,
)
from db_config import engine
from auth_utils import validate_user

router = APIRouter()


class BasicRequest(BaseModel):
    """
    Basic request model for identifying the user and the db name.
    """

    db_name: str
    token: str

    model_config = {
        "json_schema_extra": {
            "examples": [{"db_name": "my_db_name", "token": "my_token"}]
        }
    }


class ReportRequest(BasicRequest):
    """
    Request model for identifying the report to be accessed / modified.
    """

    report_id: int

    model_config = {
        "json_schema_extra": {"examples": [{"db_name": "my_db_name", "report_id": 1}]}
    }


class ReportAnalysisRequest(ReportRequest):
    """
    Request model for requesting a specific analysis of a report.
    """

    analysis_id: str

    model_config = {
        "json_schema_extra": {
            "examples": [{"db_name": "my_db_name", "report_id": 1, "analysis_id": 1}]
        }
    }


@router.post("/oracle/list_reports")
async def reports_list(req: BasicRequest):
    """
    Get the list of reports that have been generated by the user, including
    those in progress. Returns a list of dictionaries, each containing:
    - report_id
    - report_name
    - status
    - date_created
    """
    if not (await validate_user(req.token)):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    async with AsyncSession(engine) as session:
        async with session.begin():
            stmt = (
                select(
                    OracleReports.report_id,
                    OracleReports.report_name,
                    OracleReports.status,
                    OracleReports.created_ts,
                    OracleReports.inputs,
                )
                .where(OracleReports.db_name == req.db_name)
                .order_by(OracleReports.created_ts.desc())
            )
            result = await session.execute(stmt)
            reports = result.fetchall()

    reports_list = []
    for report in reports:
        status = report.status.value or ""
        is_revision = status.startswith("Revision: ")
        is_being_revised = status.startswith("Revision in progress: ")
        if is_revision:
            continue
        reports_list.append(
            {
                "report_id": report.report_id,
                "report_name": report.report_name,
                "status": report.status.value,
                "is_revision": is_revision,
                "is_being_revised": is_being_revised,
                "date_created": report.created_ts.isoformat(),  # Convert to ISO 8601 string
                "inputs": report.inputs,
            }
        )

    return JSONResponse(status_code=200, content={"reports": reports_list})


@router.post("/oracle/delete_report")
async def delete_report(req: ReportRequest):
    """
    Given a report_id, this endpoint will delete the report from the system.
    Reports in progress will have their associated background tasks cancelled.
    """
    if not (await validate_user(req.token)):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    report = None

    async with AsyncSession(engine) as session:
        async with session.begin():
            stmt = select(OracleReports).where(
                OracleReports.db_name == req.db_name,
                OracleReports.report_id == req.report_id,
            )
            result = await session.execute(stmt)
            report = result.scalar_one_or_none()
            if report:
                await session.delete(report)

    if report:
        return JSONResponse(status_code=200, content={"message": "Report deleted"})
    else:
        return JSONResponse(status_code=404, content={"error": "Report not found"})


@router.post("/oracle/get_report_mdx")
async def get_report_mdx(req: ReportRequest):
    """
    Given a report_id, this endpoint will return the MDX string for the report stored in the postgres db.

    Will return status 400 if no string is found.
    """
    if not (await validate_user(req.token)):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    async with AsyncSession(engine) as session:
        async with session.begin():
            stmt = select(OracleReports).where(
                OracleReports.db_name == req.db_name,
                OracleReports.report_id == req.report_id,
            )
            result = await session.execute(stmt)
            report = result.scalar_one_or_none()

            if report:
                analyses = report.analyses or []
                thinking_steps = report.thinking_steps or []
                report_with_citations = report.report_content_with_citations or []

                non_sql_thinking_steps = [step for step in thinking_steps if step["function_name"] != "text_to_sql_tool"]
                for idx in range(len(non_sql_thinking_steps)):
                    # add the analysis_id to the step
                    # if result is a dict, add the analysis_id to the step
                    if isinstance(non_sql_thinking_steps[idx]["result"], dict):
                        non_sql_thinking_steps[idx]["analysis_id"] = non_sql_thinking_steps[idx]["result"].get("analysis_id", "unknown")
                
                return JSONResponse(
                    status_code=200,
                    content={
                        "mdx": report.mdx,
                        "analyses": analyses + non_sql_thinking_steps,
                        "report_with_citations": report_with_citations,
                        "inputs": report.inputs,
                    },
                )
            else:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Report not found"},
                )


class UpdateReportMDXRequest(ReportRequest):
    """
    Request model for updating the MDX string for a report. This will allow us to update both the initially generated mdx and the tiptap's edited mdx.

    We separate the two because we want to keep the original mdx for exporting/revision purposes later on.
    """

    mdx: Optional[str] = None
    tiptap_mdx: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "examples": [{"db_name": "my_db_name", "report_id": 1, "mdx": "MDX string"}]
        }
    }


@router.post("/oracle/get_report_analysis_ids")
async def get_report_analysis_ids(req: ReportRequest):
    """
    Given a report_id, this endpoint will return the list of analyses ids for the report.
    """
    if not (await validate_user(req.token)):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    async with AsyncSession(engine) as session:
        async with session.begin():
            stmt = select(OracleReports).where(
                OracleReports.db_name == req.db_name,
                OracleReports.report_id == req.report_id,
            )
            result = await session.execute(stmt)
            result = result.scalar_one_or_none()
            if not result:
                return JSONResponse(
                    status_code=404, content={"error": "Report not found"}
                )

            analyses = result.analysis_ids

            return JSONResponse(status_code=200, content={"analyses": analyses})


@router.post("/oracle/get_report_status")
async def get_report_status(req: ReportRequest):
    """
    Given a report_id, this endpoint will return the status of the report.
    """
    if not (await validate_user(req.token)):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    # get the report
    async with AsyncSession(engine) as session:
        async with session.begin():
            stmt = select(OracleReports).where(
                OracleReports.db_name == req.db_name,
                OracleReports.report_id == req.report_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                return JSONResponse(
                    status_code=200,
                    content={"status": row.status},
                )
            return JSONResponse(status_code=404, content={"error": "Report not found"})


@router.post("/oracle/get_report_comments")
async def get_report_comments(req: ReportRequest):
    """
    Given a report_id, this endpoint will return the comments for the report.
    """
    if not (await validate_user(req.token)):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    async with AsyncSession(engine) as session:
        async with session.begin():
            stmt = select(OracleReports).where(
                OracleReports.db_name == req.db_name,
                OracleReports.report_id == req.report_id,
            )
            result = await session.execute(stmt)
            report = result.scalar_one_or_none()
            if report:
                return JSONResponse(
                    status_code=200, content={"comments": report.comments}
                )
            else:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Report not found"},
                )


class UpdateReportCommentsRequest(ReportRequest):
    """
    Request model for updating the comments for a report.
    """

    comments: list

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"db_name": "my_db_name", "report_id": 1, "comments": "Comments"}
            ]
        }
    }


@router.post("/oracle/update_report_comments")
async def update_report_comments(req: UpdateReportCommentsRequest):
    """
    Given a report_id, this endpoint will update the comments for the report.
    """
    if not (await validate_user(req.token)):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    async with AsyncSession(engine) as session:
        async with session.begin():
            stmt = select(OracleReports).where(
                OracleReports.db_name == req.db_name,
                OracleReports.report_id == req.report_id,
            )
            result = await session.execute(stmt)
            report = result.scalar_one_or_none()

            if report:
                report.comments = req.comments
                return JSONResponse(
                    status_code=200, content={"message": "Comments updated"}
                )
            else:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Report not found"},
                )
