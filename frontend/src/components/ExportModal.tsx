// Export books → CSV / text. Filter (missing/library/all) + format
// (csv/text), generate, copy to clipboard, download as file.
//
// Desktop renders centered 600px modal; mobile renders the same
// controls inside a full-height MobileSheet.
import { useRef, useState } from "react";
import { useTheme } from "../theme";
import { useViewport } from "../hooks/useViewport";
import {
  useMobileCodepath,
  MobileSheet,
  MobileBtn,
} from "./mobile";
import { Btn } from "./Btn";
import { Spin } from "./Spin";

interface ExportModalProps {
  onClose: () => void;
  defaultFilter?: string;
}

export function ExportModal({ onClose, defaultFilter = "missing" }: ExportModalProps) {
  const t = useTheme();
  const vp = useViewport();
  const mobile = useMobileCodepath(vp);
  const [filter, setFilter] = useState(defaultFilter);
  const [fmt, setFmt] = useState("csv");
  const [content, setContent] = useState<string | null>(null);
  const [ld, setLd] = useState(false);
  const [copied, setCopied] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const generate = async () => {
    setLd(true);
    setCopied(false);
    setDownloaded(false);
    try {
      const r = await fetch(
        `/api/discovery/export?filter=${filter}&format=${fmt}`,
      );
      const text = await r.text();
      setContent(text);
    } catch {
      setContent("Error generating export");
    }
    setLd(false);
  };

  const download = () => {
    if (!content) return;
    const blob = new Blob([content], {
      type: fmt === "csv" ? "text/csv" : "text/plain",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `books_${filter}.${fmt === "csv" ? "csv" : "txt"}`;
    a.click();
    URL.revokeObjectURL(url);
    setDownloaded(true);
    setTimeout(() => setDownloaded(false), 2000);
  };

  const copy = async () => {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      try {
        if (taRef.current) {
          taRef.current.select();
          document.execCommand("copy");
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }
      } catch { /* ignore */ }
    }
  };

  const sel = mobile
    ? {
        padding: "0 12px",
        minHeight: 44,
        borderRadius: 8,
        border: `1px solid ${t.border}`,
        background: t.inp,
        color: t.text,
        fontSize: 16,
      }
    : {
        padding: "7px 12px",
        borderRadius: 6,
        border: `1px solid ${t.border}`,
        background: t.inp,
        color: t.text2,
        fontSize: 13,
      };

  const lbl = {
    fontSize: 11,
    fontWeight: 600,
    color: t.tg,
    textTransform: "uppercase" as const,
  };

  const body = (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          alignItems: mobile ? "stretch" : "flex-end",
          flexDirection: mobile ? "column" : "row",
        }}
      >
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={lbl}>Filter</label>
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setContent(null);
            }}
            style={sel}
          >
            <option value="missing">Missing only</option>
            <option value="library">Library only</option>
            <option value="all">All books</option>
          </select>
        </div>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={lbl}>Format</label>
          <select
            value={fmt}
            onChange={(e) => {
              setFmt(e.target.value);
              setContent(null);
            }}
            style={sel}
          >
            <option value="csv">CSV</option>
            <option value="text">Text</option>
          </select>
        </div>
        {mobile ? (
          <MobileBtn
            variant="primary"
            primary
            fullWidth
            onClick={generate}
            disabled={ld}
          >
            {ld ? "Generating…" : "Generate"}
          </MobileBtn>
        ) : (
          <Btn variant="accent" onClick={generate} disabled={ld}>
            {ld ? <Spin /> : "Generate"}
          </Btn>
        )}
      </div>
      {content && (
        <>
          <div style={{ position: "relative" }}>
            <textarea
              ref={taRef}
              readOnly
              value={content}
              style={{
                width: "100%",
                height: mobile ? 200 : 240,
                padding: 12,
                background: t.bg3,
                border: `1px solid ${t.borderL}`,
                borderRadius: 8,
                color: t.text2,
                fontSize: 12,
                fontFamily: "monospace",
                resize: "vertical",
              }}
            />
          </div>
          <div
            style={{
              display: mobile ? "grid" : "flex",
              gridTemplateColumns: mobile ? "1fr 1fr" : undefined,
              gap: 8,
              justifyContent: mobile ? undefined : "flex-end",
            }}
          >
            {mobile ? (
              <>
                <MobileBtn
                  variant="secondary"
                  fullWidth
                  onClick={copy}
                  style={
                    copied ? { background: t.grn, color: "#fff", border: `1px solid ${t.grn}` } : undefined
                  }
                >
                  {copied ? "✓ Copied" : "Copy"}
                </MobileBtn>
                <MobileBtn
                  variant="primary"
                  primary
                  fullWidth
                  onClick={download}
                  style={
                    downloaded ? { background: t.grn, color: "#fff", border: `1px solid ${t.grn}` } : undefined
                  }
                >
                  {downloaded ? "✓ Saved" : `↓ Download .${fmt === "csv" ? "csv" : "txt"}`}
                </MobileBtn>
              </>
            ) : (
              <>
                <Btn
                  size="sm"
                  onClick={copy}
                  style={
                    copied ? { background: t.grn, borderColor: t.grn, color: "#fff" } : {}
                  }
                >
                  {copied ? "✓ Copied" : "Copy"}
                </Btn>
                <Btn
                  size="sm"
                  onClick={download}
                  style={
                    downloaded ? { background: t.grn, borderColor: t.grn, color: "#fff" } : {}
                  }
                >
                  {downloaded ? "✓ Downloaded" : `↓ Download .${fmt === "csv" ? "csv" : "txt"}`}
                </Btn>
              </>
            )}
          </div>
        </>
      )}
      {!content && !ld && (
        <p
          style={{
            fontSize: 13,
            color: t.tg,
            textAlign: "center",
            padding: 12,
            margin: 0,
          }}
        >
          Choose a filter and format, then Generate to preview.
        </p>
      )}
    </div>
  );

  if (mobile) {
    return (
      <MobileSheet
        open={true}
        onClose={onClose}
        title="Export Books"
        height="tall"
      >
        {body}
      </MobileSheet>
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        zIndex: 200,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        animation: "fadeOverlay 0.2s ease-out",
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="modal-panel"
        style={{
          background: t.bg2,
          border: `1px solid ${t.border}`,
          borderRadius: 12,
          padding: 24,
          animation: "fadeIn 0.2s ease-out",
          width: 600,
          maxWidth: "90vw",
          maxHeight: "85vh",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <h2 style={{ fontSize: 18, fontWeight: 700, color: t.text, margin: 0 }}>
          Export Books
        </h2>
        {body}
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Btn size="sm" onClick={onClose}>
            Close
          </Btn>
        </div>
      </div>
    </div>
  );
}
