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
    """Return a zero-height component that adds a copy button beside a dataframe."""
    marker = "metox-copy-" + re.sub(r"[^a-zA-Z0-9_-]+", "-", button_key).strip("-")
    payload = base64.b64encode(dataframe_to_clipboard_text(frame).encode("utf-8")).decode("ascii")
    marker_json = json.dumps(marker)
    payload_json = json.dumps(payload)

    return f"""<!doctype html>
<html>
<body style="margin:0">
<script>
(() => {{
  const marker = {marker_json};
  const encoded = {payload_json};
  const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
  const clipboardText = new TextDecoder("utf-8").decode(bytes);
  const copyLabel = "一键复制";
  const copiedLabel = "已复制";
  const copyIcon = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1Zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2Zm0 16H8V7h11v14Z"/></svg>';
  const copiedIcon = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="m9 16.2-3.5-3.5L4.1 14.1 9 19 20.3 7.7l-1.4-1.4L9 16.2Z"/></svg>';

  function fallbackCopy(text) {{
    const textarea = parentDocument.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    parentDocument.body.appendChild(textarea);
    textarea.select();
    const copied = parentDocument.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("copy command was rejected");
  }}

  async function copyToClipboard() {{
    const clipboard = parentWindow.navigator.clipboard;
    if (clipboard && clipboard.writeText) {{
      try {{
        await clipboard.writeText(clipboardText);
        return;
      }} catch (error) {{
        console.warn("MetoX clipboard API was rejected; using fallback", error);
      }}
    }}
    fallbackCopy(clipboardText);
  }}

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
  host.style.position = "relative";
  host.style.height = "0";
  host.style.minHeight = "0";
  host.style.overflow = "visible";
  host.style.zIndex = "1000";

  const previous = parentDocument.querySelector(`[data-metox-copy="${{marker}}"]`);
  if (previous) previous.remove();
  const button = parentDocument.createElement("button");
  button.type = "button";
  button.setAttribute("data-metox-copy", marker);
  button.setAttribute("aria-label", copyLabel);
  button.setAttribute("title", copyLabel + "（含表头，可粘贴到 Excel）");
  button.innerHTML = copyIcon;
  button.style.cssText = [
    "position:absolute",
    "top:0.75rem",
    "right:6.25rem",
    "width:1.4rem",
    "height:1.4rem",
    "display:inline-flex",
    "align-items:center",
    "justify-content:center",
    "padding:0",
    "color:inherit",
    "background:transparent",
    "border:0",
    "border-radius:0.5rem",
    "cursor:pointer",
    "pointer-events:auto",
    "z-index:1001"
  ].join(";");
  button.dataset.metoxCopyBound = "true";
  button.addEventListener("mouseenter", () => {{
    button.style.background = "rgba(128, 128, 128, 0.18)";
  }});
  button.addEventListener("mouseleave", () => {{
    button.style.background = "transparent";
  }});
  button.addEventListener("click", async event => {{
    event.preventDefault();
    event.stopPropagation();
    try {{
      await copyToClipboard();
      button.setAttribute("aria-label", copiedLabel);
      button.setAttribute("title", copiedLabel);
      button.innerHTML = copiedIcon;
      window.setTimeout(() => {{
        button.setAttribute("aria-label", copyLabel);
        button.setAttribute("title", copyLabel + "（含表头，可粘贴到 Excel）");
        button.innerHTML = copyIcon;
      }}, 1600);
    }} catch (error) {{
      console.error("MetoX dataframe copy failed", error);
      button.setAttribute("title", "复制失败，请重试");
    }}
  }});
  host.appendChild(button);
}})();
</script>
</body>
</html>"""
