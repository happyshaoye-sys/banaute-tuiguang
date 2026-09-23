"""Build the client-side copy action used by Streamlit dataframes."""
from __future__ import annotations

import base64
import json
import re

import pandas as pd


def dataframe_to_clipboard_text(frame: pd.DataFrame) -> str:
    """Return an Excel-friendly, tab-separated copy payload with headers."""
    return frame.to_csv(sep="\t", index=False, lineterminator="\n")


def build_dataframe_copy_button_html(frame: pd.DataFrame, *, button_key: str) -> str:
    """Return a zero-height component that extends the next dataframe toolbar."""
    marker = "metox-copy-" + re.sub(r"[^a-zA-Z0-9_-]+", "-", button_key).strip("-")
    payload = base64.b64encode(dataframe_to_clipboard_text(frame).encode("utf-8")).decode("ascii")
    marker_json = json.dumps(marker)
    payload_json = json.dumps(payload)
    copy_label_json = json.dumps("一键复制")
    copied_label_json = json.dumps("已复制")
    copy_title_json = json.dumps("一键复制（含表头，可粘贴到 Excel）")
    failed_title_json = json.dumps("复制失败，请重试")

    return f"""<!doctype html>
<html>
<body style="margin:0">
<script>
(() => {{
  const marker = {marker_json};
  const encoded = {payload_json};
  const copyLabel = {copy_label_json};
  const copiedLabel = {copied_label_json};
  const copyTitle = {copy_title_json};
  const failedTitle = {failed_title_json};
  const nativeToolbarLabels = new Set([
    "Show/hide columns",
    "Download as CSV",
    "Search",
    "Fullscreen"
  ]);
  const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
  const clipboardText = new TextDecoder("utf-8").decode(bytes);
  const copyPath = "M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1Zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2Zm0 16H8V7h11v14Z";
  const copiedPath = "m9 16.2-3.5-3.5L4.1 14.1 9 19 20.3 7.7l-1.4-1.4L9 16.2Z";

  let parentWindow;
  let parentDocument;
  let frame;
  let host;
  try {{
    parentWindow = window.parent;
    parentDocument = parentWindow.document;
    frame = window.frameElement;
    host = frame && frame.closest('[data-testid="stElementContainer"]');
  }} catch (error) {{
    console.error("MetoX copy component could not access the parent page", error);
    return;
  }}
  if (!host) return;

  frame.style.display = "none";
  host.style.height = "0";
  host.style.minHeight = "0";
  host.style.overflow = "hidden";

  function fallbackCopy(text) {{
    const previousFocus = parentDocument.activeElement;
    const textarea = parentDocument.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    parentDocument.body.appendChild(textarea);
    textarea.focus({{ preventScroll: true }});
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    let copied = false;
    try {{
      copied = parentDocument.execCommand("copy");
    }} finally {{
      textarea.remove();
      if (previousFocus && previousFocus.focus) {{
        previousFocus.focus({{ preventScroll: true }});
      }}
    }}
    return copied;
  }}

  async function copyToClipboard() {{
    // execCommand must run before the first await so it keeps the click activation.
    if (fallbackCopy(clipboardText)) return "execCommand";
    const clipboard = parentWindow.navigator.clipboard;
    if (clipboard && clipboard.writeText) {{
      await clipboard.writeText(clipboardText);
      return "clipboard";
    }}
    throw new Error("no supported clipboard writer");
  }}

  function findTargetDataframe() {{
    const followingFlag = parentWindow.Node.DOCUMENT_POSITION_FOLLOWING;
    const dataframes = parentDocument.querySelectorAll('[data-testid="stDataFrame"]');
    for (const dataframe of dataframes) {{
      if (host.compareDocumentPosition(dataframe) & followingFlag) return dataframe;
    }}
    return null;
  }}

  function lowestCommonAncestor(elements, stopAt) {{
    let candidate = elements[0] && elements[0].parentElement;
    while (candidate && candidate !== stopAt) {{
      if (elements.every(element => candidate.contains(element))) return candidate;
      candidate = candidate.parentElement;
    }}
    return null;
  }}

  function toolbarSlot(button, toolbar) {{
    let slot = button;
    while (slot.parentElement && slot.parentElement !== toolbar) slot = slot.parentElement;
    return slot.parentElement === toolbar ? slot : null;
  }}

  function clearDuplicateIds(root) {{
    if (root.removeAttribute) root.removeAttribute("id");
    for (const element of root.querySelectorAll("[id]")) element.removeAttribute("id");
  }}

  function renderNativeSizedIcon(button, pathData) {{
    const svg = button.querySelector("svg");
    if (!svg) return;
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.replaceChildren();
    const path = parentDocument.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("fill", "currentColor");
    path.setAttribute("d", pathData);
    svg.appendChild(path);
  }}

  function bindCopyButton(button) {{
    button.type = "button";
    button.setAttribute("data-metox-copy", marker);
    button.setAttribute("aria-label", copyLabel);
    button.setAttribute("title", copyTitle);
    button.setAttribute("data-metox-copy-state", "idle");
    renderNativeSizedIcon(button, copyPath);
    button.addEventListener("click", async event => {{
      event.preventDefault();
      event.stopPropagation();
      button.setAttribute("data-metox-copy-state", "copying");
      try {{
        const copyMethod = await copyToClipboard();
        button.setAttribute("data-metox-copy-state", "copied");
        button.setAttribute("data-metox-copy-method", copyMethod);
        button.setAttribute("aria-label", copiedLabel);
        button.setAttribute("title", copiedLabel);
        renderNativeSizedIcon(button, copiedPath);
        window.setTimeout(() => {{
          button.setAttribute("data-metox-copy-state", "idle");
          button.removeAttribute("data-metox-copy-method");
          button.setAttribute("aria-label", copyLabel);
          button.setAttribute("title", copyTitle);
          renderNativeSizedIcon(button, copyPath);
        }}, 1600);
      }} catch (error) {{
        console.error("MetoX dataframe copy failed", error);
        button.setAttribute("data-metox-copy-state", "failed");
        button.setAttribute("title", failedTitle);
      }}
    }});
  }}

  function installInNativeToolbar() {{
    const dataframe = findTargetDataframe();
    if (!dataframe) return false;

    const current = dataframe.querySelector(`[data-metox-copy="${{marker}}"]`);
    if (current) return true;
    const stale = parentDocument.querySelector(`[data-metox-copy="${{marker}}"]`);
    if (stale) {{
      const staleSlot = stale.closest('[data-metox-copy-slot]');
      if (staleSlot) staleSlot.remove();
      else stale.remove();
    }}

    const nativeButtons = Array.from(dataframe.querySelectorAll("button[aria-label]"))
      .filter(button => nativeToolbarLabels.has(button.getAttribute("aria-label")));
    if (nativeButtons.length < nativeToolbarLabels.size) return false;

    const toolbar = lowestCommonAncestor(nativeButtons, dataframe);
    if (!toolbar) return false;
    const templateButton = nativeButtons.find(
      button => button.getAttribute("aria-label") === "Download as CSV"
    ) || nativeButtons[0];
    const templateSlot = toolbarSlot(templateButton, toolbar);
    const firstSlot = toolbarSlot(nativeButtons[0], toolbar);
    if (!templateSlot || !firstSlot) return false;

    const copySlot = templateSlot.cloneNode(true);
    const copyButton = copySlot.matches("button") ? copySlot : copySlot.querySelector("button");
    if (!copyButton) return false;
    clearDuplicateIds(copySlot);
    copySlot.setAttribute("data-metox-copy-slot", marker);
    bindCopyButton(copyButton);
    toolbar.insertBefore(copySlot, firstSlot);
    return true;
  }}

  let scheduled = false;
  const scheduleInstall = () => {{
    if (scheduled) return;
    scheduled = true;
    parentWindow.requestAnimationFrame(() => {{
      scheduled = false;
      installInNativeToolbar();
    }});
  }};
  const observer = new parentWindow.MutationObserver(scheduleInstall);
  observer.observe(parentDocument.body, {{childList: true, subtree: true}});

  let attempts = 0;
  const retryTimer = window.setInterval(() => {{
    attempts += 1;
    if (installInNativeToolbar() || attempts >= 100) window.clearInterval(retryTimer);
  }}, 100);
  installInNativeToolbar();
}})();
</script>
</body>
</html>"""
