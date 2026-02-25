"""HTTP custom routes for health, artifacts, and portal views."""

from __future__ import annotations

import asyncio
import logging
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from .artifacts import (
    ensure_job_id,
    is_supported_artifact_file,
    list_job_artifacts,
    load_job_manifest,
    read_job_event_lines,
    safe_resolve_artifact_path,
    touch_path,
)
from .events import subscribe_job_events
from .portal import render_portal_html

logger = logging.getLogger(__name__)


def register_custom_routes(mcp: FastMCP) -> None:
    """Attach custom HTTP routes to an MCP server instance."""

    def _required_access_token(manifest: dict) -> str | None:
        token = manifest.get("access_token")
        if isinstance(token, str):
            token_text = token.strip()
            if token_text:
                return token_text
        return None

    def _provided_access_token(request: Request) -> str:
        query_token = str(request.query_params.get("token", "")).strip()
        if query_token:
            return query_token
        return request.headers.get("x-openfoam-job-token", "").strip()

    def _is_authorized_for_manifest(request: Request, manifest: dict) -> bool:
        required_token = _required_access_token(manifest)
        if not required_token:
            return True
        provided_token = _provided_access_token(request)
        if not provided_token:
            return False
        return secrets.compare_digest(provided_token, required_token)

    def _append_token_to_url(url: str, token: str | None) -> str:
        if not token:
            return url
        parsed = urlsplit(url)
        query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "token"]
        query_pairs.append(("token", token))
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query_pairs), parsed.fragment)
        )

    def _tokenize_artifacts(artifacts: list[dict], token: str | None) -> list[dict]:
        if not token:
            return artifacts
        tokenized: list[dict] = []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            raw_url = entry.get("url")
            if isinstance(raw_url, str) and raw_url:
                entry["url"] = _append_token_to_url(raw_url, token)
            tokenized.append(entry)
        return tokenized

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "openfoam-mcp",
            }
        )

    @mcp.custom_route("/artifacts/{path:path}", methods=["GET"])
    async def read_artifact(request: Request) -> JSONResponse | FileResponse:
        requested = str(request.path_params.get("path", ""))
        if not requested:
            return JSONResponse({"error": "缺少 artifact 路径"}, status_code=400)

        try:
            artifact_file = safe_resolve_artifact_path(requested)
        except ValueError:
            return JSONResponse({"error": "artifact 路径非法"}, status_code=400)

        job_prefix = requested.split("/", 1)[0]
        manifest = None
        try:
            safe_job_id = ensure_job_id(job_prefix)
            try:
                manifest = load_job_manifest(safe_job_id)
            except FileNotFoundError:
                manifest = None
            except ValueError:
                return JSONResponse({"error": "manifest 非法"}, status_code=400)
        except ValueError:
            manifest = None

        if manifest is not None and not _is_authorized_for_manifest(request, manifest):
            return JSONResponse({"error": "访问令牌无效或缺失"}, status_code=403)

        if not artifact_file.exists() or not artifact_file.is_file():
            return JSONResponse({"error": "文件不存在"}, status_code=404)
        if not is_supported_artifact_file(artifact_file):
            return JSONResponse({"error": "文件类型不支持"}, status_code=404)

        touch_path(artifact_file)
        logger.info("下载 artifact path=%s", artifact_file)
        return FileResponse(str(artifact_file))

    @mcp.custom_route("/portal/{job_id}", methods=["GET"])
    async def portal_view(request: Request) -> JSONResponse | HTMLResponse:
        job_id = str(request.path_params.get("job_id", ""))
        try:
            manifest = load_job_manifest(job_id)
        except FileNotFoundError:
            return JSONResponse({"error": "作业不存在"}, status_code=404)
        except ValueError as exc:
            if "manifest" in str(exc):
                return JSONResponse({"error": "manifest 非法"}, status_code=400)
            return JSONResponse({"error": "job_id 非法"}, status_code=400)

        if not _is_authorized_for_manifest(request, manifest):
            return JSONResponse({"error": "访问令牌无效或缺失"}, status_code=403)

        return HTMLResponse(render_portal_html(manifest))

    @mcp.custom_route("/jobs/{job_id}/status", methods=["GET"])
    async def job_status(request: Request) -> JSONResponse:
        job_id = str(request.path_params.get("job_id", ""))
        try:
            manifest = load_job_manifest(job_id)
        except FileNotFoundError:
            return JSONResponse({"error": "作业不存在"}, status_code=404)
        except ValueError as exc:
            if "manifest" in str(exc):
                return JSONResponse({"error": "manifest 非法"}, status_code=400)
            return JSONResponse({"error": "job_id 非法"}, status_code=400)

        if not _is_authorized_for_manifest(request, manifest):
            return JSONResponse({"error": "访问令牌无效或缺失"}, status_code=403)

        token = _required_access_token(manifest)
        if "artifacts" not in manifest:
            manifest["artifacts"] = list_job_artifacts(job_id)
        manifest["artifacts"] = _tokenize_artifacts(
            manifest.get("artifacts", []),
            token,
        )
        return JSONResponse(manifest)

    @mcp.custom_route("/jobs/{job_id}/events", methods=["GET"])
    async def job_events(request: Request) -> JSONResponse | StreamingResponse:
        job_id = str(request.path_params.get("job_id", ""))
        try:
            safe_job_id = ensure_job_id(job_id)
            manifest = load_job_manifest(safe_job_id)
        except ValueError:
            return JSONResponse({"error": "job_id 非法"}, status_code=400)
        except FileNotFoundError:
            return JSONResponse({"error": "作业不存在"}, status_code=404)

        if not _is_authorized_for_manifest(request, manifest):
            return JSONResponse({"error": "访问令牌无效或缺失"}, status_code=403)

        replay_only_raw = str(request.query_params.get("replay_only", "false")).lower()
        replay_only = replay_only_raw in {"1", "true", "yes", "on"}
        replay_lines = read_job_event_lines(safe_job_id, limit=500)

        async def _event_stream():
            for line in replay_lines:
                yield f"data: {line}\n\n"

            if replay_only:
                return

            async with subscribe_job_events(safe_job_id) as queue:
                while True:
                    try:
                        line = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"data: {line}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
