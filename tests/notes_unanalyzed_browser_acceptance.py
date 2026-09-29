"""Browser regression for notes on a real Miniflux entry with no analyses row."""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from browser_env import launch
from ops_common import client, local_admin

WORKTREE = Path(__file__).resolve().parents[1]
PRODUCTION = Path("/home/ubuntu/ai-news")
PYTHON = PRODUCTION / "runtime/venv/bin/python"
BASE = "http://127.0.0.1:8093"
APP = BASE + "/inbox"
report = {"at": time.time(), "checks": []}


def check(name, passed, detail=None):
    report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(name)


username, password = local_admin()
with client() as production_reader:
    response = production_reader.get("/v1/entries", params={"limit": 1})
    response.raise_for_status()
    target = response.json()["entries"][0]
entry_id = int(target["id"])
temp_root = Path(tempfile.mkdtemp(prefix="ai-news-note-browser-", dir="/tmp"))
process = None
log_handle = None
try:
    build_parent = temp_root / "upstream/reactflux"
    build_parent.mkdir(parents=True)
    (build_parent / "build").symlink_to(
        PRODUCTION / "upstream/reactflux/build", target_is_directory=True
    )
    env = os.environ.copy()
    env.update({
        "AI_NEWS_ROOT": str(temp_root),
        "PYTHONPATH": str(WORKTREE / "src"),
        "PYTHONUNBUFFERED": "1",
    })
    env.pop("MINIFLUX_API_KEY", None)
    env.pop("ARK_API_KEY", None)
    subprocess.run(
        [
            str(PYTHON), "-c",
            "import core; core.init_db(); core.init_usage(); core.migrate(); "
            "core.save_settings({'enabled':False,'translation_enabled':False})",
        ],
        cwd=WORKTREE / "src", env=env, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    log_handle = (temp_root / "candidate.log").open("w")
    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "api:app", "--host", "127.0.0.1",
         "--port", "8093", "--no-access-log"],
        cwd=WORKTREE / "src", env=env, stdout=log_handle, stderr=subprocess.STDOUT,
    )
    for _ in range(100):
        try:
            if httpx.get(APP + "/", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("candidate server did not become ready")
    api_client = httpx.Client(
        base_url=BASE + "/mf", auth=(username, password), timeout=20, trust_env=False
    )
    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        context.add_init_script("""localStorage.setItem('settings', JSON.stringify({
            ...JSON.parse(localStorage.getItem('settings') || '{}'),
            showStatus:'all', markReadOnScroll:false, pageSize:20,
            orderBy:'published_at', orderDirection:'desc'
        }))""")
        page = context.new_page()
        page.goto(APP + "/login", wait_until="domcontentloaded")
        page.locator("#username_input").fill(username)
        page.locator("#password_input").fill(password)
        page.get_by_role("button", name="登录", exact=True).click()
        page.wait_for_url("**/all", timeout=30000)

        page.goto(APP + f"/all/entry/{entry_id}", wait_until="domcontentloaded")
        note = page.get_by_role("textbox", name="我的笔记", exact=True)
        expect(note).to_be_enabled(timeout=30000)
        check("note_editor_enabled_without_analysis", True)
        sentinel = "未分析文章笔记浏览器回归 " + str(int(time.time()))
        note.fill(sentinel)
        note.press("Control+s")
        expect(page.locator(".article-note-head small")).to_contain_text("已保存", timeout=15000)

        stored = api_client.get(f"/v1/ai/notes/{entry_id}")
        check("note_saved_without_analysis", stored.status_code == 200 and stored.json()["note"] == sentinel)

        page.get_by_role("button", name="关闭文章", exact=True).click()
        page.get_by_role("button", name="全部原始", exact=True).click()
        with page.expect_response(
            lambda response: response.url.startswith(BASE + "/mf/v1/entries")
            and "ai_view=notes" in response.url,
            timeout=30000,
        ):
            page.get_by_role("button", name="有笔记", exact=True).click()
        card = page.locator(f'[data-entry-id="{entry_id}"]').first
        expect(card).to_be_visible(timeout=30000)
        check("notes_view_contains_unanalyzed_entry", True)

        card.click()
        expect(page.get_by_role("textbox", name="我的笔记", exact=True)).to_have_value(sentinel, timeout=30000)
        check("note_reloads_in_article", True)
        browser.close()
    api_client.close()

    report["passed"] = all(item["passed"] for item in report["checks"])
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)
finally:
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if log_handle is not None:
        log_handle.close()
    shutil.rmtree(temp_root, ignore_errors=True)
