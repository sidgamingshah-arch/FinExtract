"""Kensho Extract adapter for the DocumentEngine port.

Kensho Extract (S&P Global) is sent a PDF and returns its content as a tree — titles, text
blocks, tables and their cells — with each block's position. This adapter submits the PDF,
polls for the answer and maps it to positioned WORDS, the unit every reader in this pipeline
takes, so a page Kensho read goes through the same row reconstruction as a native or OCR page.

The output shape is Kensho's published one (``ExtractOutputModel`` in Kensho's own open-source
converter, kensho-technologies/kenverters): ``content_tree`` of nodes ``{uid, type, content,
children, locations, text_node_data}``, where a location is ``{x, y, width, height,
page_number}`` as fractions of the page, top-left origin, pages numbered from 0, and
``text_node_data`` carries the text split by line with each character's offset across its box.
A result may arrive wrapped as ``{status, output, error, metadata}``; both forms are accepted.

The ADDRESSES and the token are configuration (``[document_engine]``): they are specific to a
Kensho account and documented to its holder, so nothing here assumes them. The token is read at
call time from an environment variable and never stored. Lazy: importing this module touches no
network and needs no token.
"""
from __future__ import annotations

import os
import re
import time

from app.adapters._structured import LlmConfigError
from app.core.models.geometry import BBox
from app.ports.ocr import OcrWord

__all__ = ["KenshoExtractProvider", "kensho_status", "output_of", "pages_of", "words_by_page"]

# A word is a run of non-space characters. Page headers and footers are kept like any other
# text: the pipeline's own chrome filter decides what is chrome, the same way for every engine.
_SPACE = re.compile(r"\S+")


def _clamp(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def output_of(payload: dict) -> dict | None:
    """The Extract document inside a result body, or None when the body holds none yet."""
    if not isinstance(payload, dict):
        return None
    if "content_tree" in payload:
        return payload
    inner = payload.get("output")
    if isinstance(inner, dict) and "content_tree" in inner:
        return inner
    return None


def _split(text: str, loc: dict, offsets: list[float] | None) -> list[OcrWord]:
    """One line of text in one box → its words, each with its own box.

    With Kensho's character offsets (each character's start as a fraction of the box width) a
    word is placed exactly; without them the box is divided by character count, which is exact
    for a cell holding one figure and an estimate for a longer line."""
    x, y = float(loc.get("x", 0.0)), float(loc.get("y", 0.0))
    w, h = float(loc.get("width", 0.0)), float(loc.get("height", 0.0))
    n = len(text)
    if n == 0 or w <= 0 or h <= 0:
        return []
    use = offsets if offsets and len(offsets) == n else None
    words: list[OcrWord] = []
    for m in _SPACE.finditer(text):
        a, b = m.start(), m.end()
        if use is not None:
            f0 = use[a]
            f1 = use[b] if b < n else 1.0
        else:
            f0, f1 = a / n, b / n
        words.append({"text": m.group(0),
                      "bbox": BBox(x0=_clamp(x + f0 * w), y0=_clamp(y),
                                   x1=_clamp(x + f1 * w), y1=_clamp(y + h)),
                      "confidence": 1.0})
    return words


def words_by_page(payload: dict) -> dict[int, list[OcrWord]]:
    """Map an Extract result to positioned words by 0-based page index.

    Every node carrying text contributes, whatever its type: a table's figures are its cells'
    text, placed where Kensho saw them, so the reader finds the columns from the positions as it
    does on a native page. A node with ``text_node_data`` is read line by line from it; one
    without is read from its ``content`` in its first location."""
    doc = output_of(payload)
    pages: dict[int, list[OcrWord]] = {}
    if doc is None:
        return pages

    def add(loc: dict | None, text: str, offsets) -> None:
        if not loc or not text or not text.strip():
            return
        pages.setdefault(int(loc.get("page_number", 0)), []).extend(_split(text, loc, offsets))

    stack = [doc["content_tree"]]
    while stack:
        node = stack.pop()
        kids = node.get("children") or []
        stack.extend(reversed(kids))
        tnd = node.get("text_node_data") or {}
        texts = tnd.get("texts")
        if texts:
            locs = tnd.get("text_locations") or []
            offs = tnd.get("character_offsets") or []
            for i, text in enumerate(texts):
                add(locs[i] if i < len(locs) else None, text, offs[i] if i < len(offs) else None)
            continue
        if kids:            # a container (document, table): its text is its children's
            continue
        locs = node.get("locations") or []
        add(locs[0] if locs else None, node.get("content") or "", None)
    for words in pages.values():
        words.sort(key=lambda wd: (round(wd["bbox"].y0, 3), wd["bbox"].x0))
    return pages


def pages_of(payload: dict) -> dict[int, list[OcrWord]]:
    """`words_by_page`, refusing an answer that has text but no positions: that is what Kensho
    returns when `output_format` does not ask for locations, and without positions no figure can
    be put in its column — so it is a configuration error, not an empty filing."""
    pages = words_by_page(payload)
    if pages:
        return pages
    stack = [output_of(payload)["content_tree"]]
    while stack:
        node = stack.pop()
        if (node.get("content") or "").strip():
            raise LlmConfigError(
                "Kensho Extract returned text without positions. Set [document_engine]."
                "kensho_params.output_format = \"structured_document_with_locations\".")
        stack.extend(node.get("children") or [])
    return pages


def kensho_status(settings=None) -> dict:
    """What the Settings screen shows: whether the addresses are set and a token is available —
    never the token."""
    from app.config import get_settings

    cfg = (settings or get_settings()).document_engine
    refresh = bool(cfg.kensho_token_url) and bool(os.environ.get(cfg.kensho_refresh_token_env))
    return {"submit_url": cfg.kensho_submit_url, "result_url": cfg.kensho_result_url,
            "addresses_set": bool(cfg.kensho_submit_url and cfg.kensho_result_url),
            "token_env": cfg.kensho_refresh_token_env if cfg.kensho_token_url else cfg.kensho_token_env,
            "token_present": refresh or bool(os.environ.get(cfg.kensho_token_env))}


class KenshoExtractProvider:
    id = "kensho"

    def __init__(self, settings=None):
        from app.config import get_settings

        self._settings = settings or get_settings()

    @property
    def _cfg(self):
        return self._settings.document_engine

    def _token(self, client) -> str:
        cfg = self._cfg
        if cfg.kensho_token_url:
            refresh = os.environ.get(cfg.kensho_refresh_token_env)
            if not refresh:
                raise LlmConfigError(
                    f"Kensho token exchange is configured but {cfg.kensho_refresh_token_env} is "
                    f"not set ([document_engine].kensho_refresh_token_env).")
            form = {"grant_type": "refresh_token", "refresh_token": refresh}
            if cfg.kensho_client_id:
                form["client_id"] = cfg.kensho_client_id
            res = client.post(cfg.kensho_token_url, data=form)
            res.raise_for_status()
            token = res.json().get("access_token")
            if not token:
                raise LlmConfigError("Kensho token exchange returned no access_token.")
            return token
        token = os.environ.get(cfg.kensho_token_env)
        if not token:
            raise LlmConfigError(
                f"No Kensho access token: set the {cfg.kensho_token_env} environment variable "
                f"([document_engine].kensho_token_env), or configure kensho_token_url to "
                f"exchange a refresh token.")
        return token

    def _addresses(self) -> tuple[str, str]:
        cfg = self._cfg
        if not (cfg.kensho_submit_url and cfg.kensho_result_url):
            raise LlmConfigError(
                "Kensho Extract addresses are not set: configure [document_engine]."
                "kensho_submit_url and kensho_result_url in config.toml from Kensho's API "
                "documentation for your account.")
        return cfg.kensho_submit_url, cfg.kensho_result_url

    def read_pdf(self, data: bytes) -> dict[int, list[OcrWord]]:  # pragma: no cover - needs Kensho + network
        import httpx

        submit_url, result_url = self._addresses()
        cfg = self._cfg
        with httpx.Client(timeout=120.0) as client:
            headers = {"Authorization": f"Bearer {self._token(client)}"}
            started = client.post(submit_url, headers=headers, data=dict(cfg.kensho_params),
                                  files={cfg.kensho_file_field: ("filing.pdf", data, "application/pdf")})
            started.raise_for_status()
            body = started.json()
            if output_of(body) is not None:          # answered synchronously
                return pages_of(body)
            request_id = body.get("request_id") or body.get("id")
            if not request_id:
                raise LlmConfigError(f"Kensho submit returned no request_id: {sorted(body)}")
            deadline = time.monotonic() + cfg.kensho_timeout_seconds
            while time.monotonic() < deadline:
                time.sleep(cfg.kensho_poll_seconds)
                poll = client.get(result_url.replace("{request_id}", str(request_id)),
                                  headers=headers)
                poll.raise_for_status()
                body = poll.json()
                if output_of(body) is not None:
                    return pages_of(body)
                if body.get("error") or str(body.get("status", "")).lower() in {"failed", "error"}:
                    raise LlmConfigError(f"Kensho Extract failed: {body.get('error') or body.get('status')}")
            raise LlmConfigError(f"Kensho Extract gave no result within {cfg.kensho_timeout_seconds:.0f}s.")
