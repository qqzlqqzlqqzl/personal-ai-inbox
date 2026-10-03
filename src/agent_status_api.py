"""Admin-only, cache-only Reader endpoint. It never contacts GitHub or starts jobs."""
import os
from datetime import datetime,timezone
from pathlib import Path
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from agent_status_store import read_store,response

def unknown():
    return {'sample':None,'last_successful_pull_at':None,'last_attempt_at':None,'pull_status':'unknown','freshness':'unknown','stale_after_seconds':1800,'target_interval_seconds':600}

def create_router(authorize):
    router=APIRouter()
    @router.get('/mf/v1/ai/agent-status')
    async def status(request:Request):
        await authorize(request,admin=True)
        configured=os.environ.get('AGENT_STATUS_STATE_PATH')
        value=unknown()
        if configured:
            try:
                path=Path(configured)
                if not path.is_absolute() or path.stat().st_size>131072:raise ValueError('cache')
                value=response(read_store(path),datetime.now(timezone.utc))
            except (ValueError,KeyError,TypeError,OSError):pass
        return JSONResponse(value,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    return router