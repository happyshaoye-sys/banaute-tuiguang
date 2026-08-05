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
    """Return a zero-height component that adds a copy action to the next dataframe."""
    marker = "metox-copy-" + re.sub(r"[^a-zA-Z0-9_-]+", "-", button_key).strip("-")
    payload = base64.b64encode(dataframe_to_clipboard_text(frame).encode("utf-8")).decode("ascii")
    marker_json = json.dumps(marker)
    payload_json = json.dumps(payload)

    return f"""<script>
(() => {{
  const hostScript = document.currentScript;
  const marker = {marker_json};
  const encoded = {payload_json};
  const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
  const clipboardText = new TextDecoder("utf-8").decode(bytes);
  const copyLabel = "一键复制";
  const copiedLabel = "已复制";
  const copyIcon = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1Zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2Zm0 16H8V7h11v14Z"/></svg>';
  const copiedIcon = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="m9 16.2-3.5-3.5L4.1 14.1 9 19 20.3 7.7l-1.4-1.4L9 16.2Z"/></svg>';

  function fallbackCopy(text) {{
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("copy command was rejected");
  }}

  async function copyToClipboard() {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      await navigator.clipboard.writeText(clipboardText);
      return;
    }}
    fallbackCopy(clipboardText);
  }}

  function findTargetDataframe() {{
    let container = hostScript && hostScript.closest('[data-testid="stElementContainer"]');
    let sibling = container && container.nextElementSibling;
    while (sibling) {{
      const dataframe = sibling.matches('[data-testid="stDataFrame"]')
        ? sibling
        : sibling.querySelector('[data-testid="stDataFrame"]');
      if (dataframe) return dataframe;
      sibling = sibling.nextElementSibling;
    }}
    return null;
  }}

  function installCopyButton() {{
    let dataframe;
    try {{
      dataframe = findTargetDataframe();
    }} catch (error) {{
      console.warn("MetoX copy button could not access the dataframe", error);
      return false;
    }}
    if (!dataframe) return false;
    const downloadButton = dataframe.querySelector('button[aria-label="Download as CSV"]');
    const toolbar = downloadButton && downloadButton.parentElement;
    if (!toolbar) return false;
    if (toolbar.querySelector(`[data-metox-copy="${{marker}}"]`)) return true;

    const button = downloadButton.cloneNode(false);
    button.type = "button";
    button.removeAttribute("id");
    button.setAttribute("data-metox-copy", marker);
    button.setAttribute("aria-label", copyLabel);
    button.setAttribute("title", copyLabel + "（含表头，可粘贴到 Excel）");
    button.innerHTML = copyIcon;
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
    toolbar.appendChild(button);
    return true;
  }}

  let attempts = 0;
  const timer = window.setInterval(() => {{
    attempts += 1;
    if (installCopyButton() || attempts >= 100) window.clearInterval(timer);
  }}, 100);
  const observer = new MutationObserver(installCopyButton);
  observer.observe(document.body, {{childList: true, subtree: true}});
}})();
</script>"""
