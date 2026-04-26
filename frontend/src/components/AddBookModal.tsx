// Manual book entry — title/author/series/dates/ISBN/description.
//
// Desktop renders a centered 460px modal. Mobile renders the same
// form fields inside a full-height MobileSheet with sticky footer.
import { useState, type CSSProperties } from "react";
import { useTheme } from "../theme";
import { api } from "../api";
import { useViewport } from "../hooks/useViewport";
import {
  useMobileCodepath,
  MobileSheet,
  MobileBtn,
} from "./mobile";
import { Btn } from "./Btn";
import { Spin } from "./Spin";

interface AddBookModalProps {
  onClose: () => void;
  onAdded?: () => void;
}

interface BookForm {
  title: string;
  author_name: string;
  series_name: string;
  series_index: string;
  pub_date: string;
  expected_date: string;
  description: string;
  isbn: string;
  is_unreleased: boolean;
}

const EMPTY: BookForm = {
  title: "",
  author_name: "",
  series_name: "",
  series_index: "",
  pub_date: "",
  expected_date: "",
  description: "",
  isbn: "",
  is_unreleased: false,
};

export function AddBookModal({ onClose, onAdded }: AddBookModalProps) {
  const t = useTheme();
  const vp = useViewport();
  const mobile = useMobileCodepath(vp);
  const [f, setF] = useState<BookForm>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const upF = <K extends keyof BookForm>(field: K, val: BookForm[K]) =>
    setF((prev) => ({ ...prev, [field]: val }));

  const save = async () => {
    if (!f.title || !f.author_name) {
      setErr("Title and author are required");
      return;
    }
    setSaving(true);
    try {
      await api.post("/discovery/books/add", f);
      onAdded?.();
      onClose();
    } catch {
      setErr("Failed to add");
    }
    setSaving(false);
  };

  const ist: CSSProperties = mobile
    ? {
        padding: "10px 12px",
        background: t.inp,
        border: `1px solid ${t.border}`,
        borderRadius: 8,
        color: t.text,
        fontSize: 16,
        width: "100%",
        minHeight: 44,
      }
    : {
        padding: "8px 10px",
        background: t.inp,
        border: `1px solid ${t.border}`,
        borderRadius: 6,
        color: t.text2,
        fontSize: 13,
        width: "100%",
      };
  const lbl: CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    color: t.tg,
    textTransform: "uppercase",
  };

  const formBody = (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <label style={lbl}>Title *</label>
        <input
          value={f.title}
          onChange={(e) => upF("title", e.target.value)}
          style={ist}
        />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <label style={lbl}>Author *</label>
        <input
          value={f.author_name}
          onChange={(e) => upF("author_name", e.target.value)}
          style={ist}
        />
      </div>
      <div style={{ display: "flex", gap: 10 }}>
        <div style={{ flex: 2, display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={lbl}>Series</label>
          <input
            value={f.series_name}
            onChange={(e) => upF("series_name", e.target.value)}
            style={ist}
          />
        </div>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={lbl}>#</label>
          <input
            type="number"
            value={f.series_index}
            onChange={(e) => upF("series_index", e.target.value)}
            style={ist}
          />
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: mobile ? "wrap" : "nowrap" }}>
        <div style={{ flex: 1, minWidth: mobile ? "100%" : 0, display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={lbl}>Pub date</label>
          <input
            type="date"
            value={f.pub_date}
            onChange={(e) => upF("pub_date", e.target.value)}
            style={ist}
          />
        </div>
        <div style={{ flex: 1, minWidth: mobile ? "100%" : 0, display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={lbl}>Expected date</label>
          <input
            type="date"
            value={f.expected_date}
            onChange={(e) => upF("expected_date", e.target.value)}
            style={ist}
          />
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <label style={lbl}>ISBN</label>
        <input
          value={f.isbn}
          onChange={(e) => upF("isbn", e.target.value)}
          style={ist}
        />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <label style={lbl}>Description</label>
        <input
          value={f.description}
          onChange={(e) => upF("description", e.target.value)}
          style={ist}
        />
      </div>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          minHeight: mobile ? 44 : undefined,
          cursor: "pointer",
        }}
      >
        <input
          type="checkbox"
          checked={f.is_unreleased}
          onChange={(e) => upF("is_unreleased", e.target.checked)}
          style={{ width: mobile ? 20 : 14, height: mobile ? 20 : 14 }}
        />
        <span style={{ fontSize: mobile ? 14 : 13, color: t.text2 }}>
          Unreleased / Upcoming
        </span>
      </label>
      {err && <div style={{ color: t.redt, fontSize: 12 }}>{err}</div>}
    </div>
  );

  if (mobile) {
    return (
      <MobileSheet
        open={true}
        onClose={onClose}
        title="Add Book"
        height="tall"
        footer={
          <>
            <MobileBtn variant="ghost" fullWidth onClick={onClose}>
              Cancel
            </MobileBtn>
            <MobileBtn
              variant="primary"
              primary
              fullWidth
              onClick={save}
              disabled={saving}
            >
              {saving ? "Adding…" : "Add Book"}
            </MobileBtn>
          </>
        }
      >
        {formBody}
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
          width: 460,
          maxWidth: "90vw",
          maxHeight: "80vh",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <h2 style={{ fontSize: 18, fontWeight: 700, color: t.text, margin: 0 }}>
          Add Book
        </h2>
        {formBody}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Btn variant="ghost" onClick={onClose}>
            Cancel
          </Btn>
          <Btn variant="accent" onClick={save} disabled={saving}>
            {saving ? <Spin /> : "Add Book"}
          </Btn>
        </div>
      </div>
    </div>
  );
}
