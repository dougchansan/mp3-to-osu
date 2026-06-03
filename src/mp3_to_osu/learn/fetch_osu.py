"""Fetch a mapper's ranked .osu files via the OFFICIAL osu! API v2.

No website scraping, no browser session - this uses the sanctioned API with
your own OAuth app (client-credentials / guest token, public scope) to list a
mapper's ranked beatmapsets, then downloads only the `.osu` text for each
standard difficulty from the public per-beatmap endpoint, politely rate
limited and cached. That `.osu` text is all StyleProfile training needs.

Credentials come from env vars so they never touch a transcript:
  OSU_CLIENT_ID, OSU_CLIENT_SECRET
Create the app at: https://osu.ppy.sh/home/account/edit  -> "OAuth"
(redirect URL can be anything, e.g. http://localhost).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

_TOKEN_URL = "https://osu.ppy.sh/oauth/token"
_API = "https://osu.ppy.sh/api/v2"
_RAW = "https://osu.ppy.sh/osu/{}"          # public raw .osu, no auth
_UA = "mp3-to-osu/0.1 (personal style analysis)"


class FetchError(RuntimeError):
    pass


def _req(url: str, *, data=None, headers=None, timeout=30) -> bytes:
    r = urllib.request.Request(url, data=data,
                               headers=headers or {}, method=None)
    r.add_header("User-Agent", _UA)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} for {url}: "
                         f"{e.read()[:200].decode('utf-8', 'ignore')}")
    except urllib.error.URLError as e:
        raise FetchError(f"network error for {url}: {e}")


def get_token(client_id: str, client_secret: str) -> str:
    body = json.dumps({
        "client_id": int(client_id),
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "public",
    }).encode()
    out = _req(_TOKEN_URL, data=body,
               headers={"Content-Type": "application/json",
                        "Accept": "application/json"})
    tok = json.loads(out).get("access_token")
    if not tok:
        raise FetchError("no access_token in token response")
    return tok


def _api_get(token: str, path: str) -> dict:
    out = _req(_API + path,
               headers={"Authorization": f"Bearer {token}",
                        "Accept": "application/json"})
    return json.loads(out)


def resolve_user_id(token: str, name: str) -> int:
    u = _api_get(token, f"/users/{urllib.parse.quote(name)}/osu"
                         "?key=username")
    uid = u.get("id")
    if not uid:
        raise FetchError(f"could not resolve osu! user {name!r}")
    return int(uid)


def ranked_beatmap_ids(token: str, user_id: int,
                       max_sets: int | None = None) -> list[int]:
    """Std-mode beatmap (difficulty) ids across a mapper's ranked sets."""
    ids: list[int] = []
    offset = 0
    while True:
        page = _api_get(
            token,
            f"/users/{user_id}/beatmapsets/ranked"
            f"?limit=50&offset={offset}")
        if not page:
            break
        for st in page:
            for bm in st.get("beatmaps", []):
                if bm.get("mode") == "osu" and bm.get("id"):
                    ids.append(int(bm["id"]))
        offset += len(page)
        if len(page) < 50 or (max_sets and offset >= max_sets):
            break
        time.sleep(0.5)                         # be polite
    return ids


def download_osu(beatmap_id: int, dest_dir: str) -> str | None:
    os.makedirs(dest_dir, exist_ok=True)
    fp = os.path.join(dest_dir, f"{beatmap_id}.osu")
    if os.path.isfile(fp) and os.path.getsize(fp) > 200:
        return fp                                # cached
    try:
        data = _req(_RAW.format(beatmap_id))
    except FetchError:
        return None
    if len(data) < 200:                          # empty / not available
        return None
    with open(fp, "wb") as fh:
        fh.write(data)
    return fp


def fetch_mapper(client_id: str, client_secret: str, mapper: str,
                 out_root: str, max_sets: int | None = None,
                 log=print) -> str:
    """Download `mapper`'s ranked .osu files into out_root/<mapper>/.
    Returns that directory (usable as a corpus root for `analyze`)."""
    token = get_token(client_id, client_secret)
    uid = resolve_user_id(token, mapper)
    log(f"  {mapper}: user id {uid}, listing ranked sets…")
    ids = ranked_beatmap_ids(token, uid, max_sets)
    log(f"  {mapper}: {len(ids)} standard difficulties found")
    dest = os.path.join(out_root, mapper)
    got = 0
    for i, bid in enumerate(ids, 1):
        if download_osu(bid, dest):
            got += 1
        if i % 25 == 0:
            log(f"  {mapper}: {i}/{len(ids)} ({got} saved)")
        time.sleep(0.4)                          # rate limit the raw endpoint
    log(f"  {mapper}: done, {got} .osu files in {dest}")
    return dest
