"""Tiny stdlib HTTP helper. No third-party deps so the Pi needs nothing but python3."""
import gzip, json, time, urllib.error, urllib.request

UA = "Mozilla/5.0 (compatible; job-agent/1.0; personal job alert bot)"


def get(url, headers=None, timeout=30, retries=2):
    hdrs = {"User-Agent": UA, "Accept": "application/json,text/*;q=0.9", "Accept-Encoding": "gzip"}
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (404, 401, 403):
                return None
        except Exception as e:  # timeouts, DNS, resets
            last = e
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    print(f"    ! fetch failed {url}: {last}")
    return None


def get_json(url, headers=None, timeout=30, retries=2):
    body = get(url, headers=headers, timeout=timeout, retries=retries)
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None
