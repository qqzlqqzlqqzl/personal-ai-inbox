"""Local maintenance helpers. Credentials never appear in arguments or reports."""

import os
import httpx
from initialize_secrets import read_env, ENV

GATEWAY = "http://127.0.0.1:8092"


def client(timeout=60):
    values = read_env("ai.env")
    return httpx.Client(
        base_url=GATEWAY + "/mf",
        headers={"X-Auth-Token": values["MINIFLUX_API_KEY"]},
        timeout=timeout,
        trust_env=False,
    )


def load_worker_environment():
    values = read_env("ai.env")
    for name in ("ARK_API_KEY", "MINIFLUX_API_KEY"):
        os.environ[name] = values[name]


def local_admin():
    values = read_env("miniflux.env")
    return values["ADMIN_USERNAME"], values["ADMIN_PASSWORD"]
