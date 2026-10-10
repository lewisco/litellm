#!/usr/bin/env bash
set -euo pipefail

readonly source_sha=8daedbc4ed501f52b8ec1569684e3b82fb60b92f
readonly image="${IMAGE_TAG:?Set IMAGE_TAG to the image to verify}"
readonly source_dir="$(cd -- "${SOURCE_DIR:?Set SOURCE_DIR to the approved source archive}" && pwd)"
readonly proof_dir="${PROOF_DIR:?Set PROOF_DIR to the smoke output directory}"
readonly smoke_dir="$(mktemp -d)"
readonly proxy_name="litellm-customers-smoke-$(basename "$smoke_dir")"

cleanup() {
  readonly status=$?
  if [ "$status" -ne 0 ]; then
    docker logs "$proxy_name" --tail 100 2>/dev/null || true
  fi
  docker rm -f "$proxy_name" >/dev/null 2>&1 || true
  rm -rf -- "$smoke_dir"
}
trap cleanup EXIT
mkdir -p "$proof_dir"
test "$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
test "$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$source_sha"

python3 - "$source_dir" > "$proof_dir/source-hashes.txt" <<'PY'
import hashlib
import sys
from pathlib import Path
from typing import Final

source: Final = Path(sys.argv[1])
paths: Final = (
    "litellm/proxy/management_endpoints/customer_endpoints.py",
    "litellm/proxy/management_helpers/object_permission_utils.py",
    "litellm/proxy/common_utils/user_api_key_cache.py",
    "enterprise/enterprise_hooks/blocked_user_list.py",
)
for relative in paths:
    print(f"{hashlib.sha256((source / relative).read_bytes()).hexdigest()}  {relative}")
PY

cat > "$smoke_dir/proxy.yaml" <<'YAML'
model_list: []
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
YAML
docker create --name "$proxy_name" --platform linux/amd64 --network none \
  --env LITELLM_LOCAL_MODEL_COST_MAP=true --env LITELLM_MASTER_KEY=sk-image-smoke-test \
  "$image" --config /tmp/smoke.yaml --port 4000 >/dev/null
docker cp "$smoke_dir/proxy.yaml" "$proxy_name:/tmp/smoke.yaml"
docker cp "$proof_dir/source-hashes.txt" "$proxy_name:/tmp/source-hashes.txt"
docker start "$proxy_name" >/dev/null
docker exec --interactive "$proxy_name" python - <<'PY' | tee "$proof_dir/smoke.json"
import hashlib
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import litellm
from litellm.rust_bridge import get_native_bridge

package_root: Final = Path(litellm.__file__).parent
source_hashes: Final = Path("/tmp/source-hashes.txt").read_text().splitlines()
assert source_hashes
for record in source_hashes:
    expected, _, relative = record.partition("  ")
    installed: Final = (
        package_root / relative.removeprefix("litellm/")
        if relative.startswith("litellm/")
        else Path("/app") / relative
    )
    assert hashlib.sha256(installed.read_bytes()).hexdigest() == expected, relative
assert get_native_bridge() is not None
assert (package_root / "proxy/_experimental/out/customers/index.html").is_file()

base: Final = "http://127.0.0.1:4000"


def request(path: str, authenticated: bool = False) -> tuple[int, bytes]:
    headers: Final = {"Authorization": "Bearer sk-image-smoke-test"} if authenticated else {}
    with urlopen(Request(base + path, headers=headers), timeout=5) as response:
        return response.status, response.read()


def wait_ready() -> None:
    for attempt in range(90):
        try:
            assert request("/health/readiness")[0] == 200
            return
        except (URLError, TimeoutError):
            if attempt == 89:
                raise
            time.sleep(1)


wait_ready()
try:
    request("/v1/models")
except HTTPError as error:
    assert error.code == 401, error.code
else:
    raise AssertionError("Model listing accepted an unauthenticated request")
assert request("/v1/models", authenticated=True)[0] == 200
ui_status, ui_html = request("/ui/customers/")
assert ui_status == 200
assert b"<!DOCTYPE html" in ui_html or b"<!doctype html" in ui_html
scripts: Final = tuple(unescape(path) for path in re.findall(r'<script[^>]+src="([^"]+)"', ui_html.decode()))
assert scripts
for script in scripts:
    location: Final = urlsplit(script)
    assert not location.scheme and not location.netloc and script.startswith("/"), script
    status, body = request(script)
    assert status == 200 and body, script
print(json.dumps({
    "verified_source_files": len(source_hashes),
    "native_extension": "loaded",
    "readiness": "passed",
    "authentication": "passed",
    "customers_page": "passed",
    "customers_scripts": len(scripts),
}))
PY
docker image inspect "$image" \
  --format '{"id": {{json .Id}}, "platform": "{{.Os}}/{{.Architecture}}", "source": {{json (index .Config.Labels "org.opencontainers.image.revision")}}, "tags": {{json .RepoTags}}}' \
  > "$proof_dir/image-metadata.json"
