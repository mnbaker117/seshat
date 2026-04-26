// Mobile-native database browser. Read-only view + per-row delete.
//
// Inline cell editing (the desktop killer feature) doesn't fit a
// phone's keyboard or screen — user can do that on desktop. On
// mobile we surface table browsing + search + delete since those
// translate cleanly to touch.
import { useEffect, useState } from "react";
import { api } from "../api";
import { useTheme } from "../theme";
import { Ic } from "../icons";
import {
  MobileInput,
  MobilePagination,
  MobileSheet,
  MobileRow,
  MobileBtn,
  MobileBackButton,
  MobileChip,
} from "../components/mobile";

interface TableEntry {
  name: string;
  row_count: number;
}

interface SchemaResponse {
  table: string;
  columns: { name: string; type: string; primary_key: boolean }[];
}

interface RowsResponse {
  table: string;
  total: number;
  page: number;
  per_page: number;
  rows: Record<string, unknown>[];
}

const PER_PAGE = 25;

export default function MobileDatabasePage() {
  const t = useTheme();
  const [tables, setTables] = useState<TableEntry[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tableSheet, setTableSheet] = useState(false);

  useEffect(() => {
    api
      .get<{ tables: TableEntry[] }>("/v1/db/tables")
      .then((r) => setTables(r.tables))
      .catch((e) => setError(String(e)));
  }, []);

  const loadTable = async (name: string) => {
    setSelected(name);
    setSearch("");
    setPage(1);
    try {
      const s = await api.get<SchemaResponse>(`/v1/db/table/${name}/schema`);
      setSchema(s);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    const params = new URLSearchParams({
      page: String(page),
      per_page: String(PER_PAGE),
    });
    if (search) params.set("search", search);
    api
      .get<RowsResponse>(`/v1/db/table/${selected}?${params}`)
      .then((r) => {
        setRows(r.rows);
        setTotal(r.total);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [selected, page, search]);

  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  const pkCol = schema?.columns.find((c) => c.primary_key)?.name || "id";

  const deleteRow = async (rowKey: unknown) => {
    if (!confirm(`Delete row ${rowKey} from ${selected}?`)) return;
    try {
      await api.del(`/v1/db/table/${selected}/row/${rowKey}`);
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(PER_PAGE),
      });
      if (search) params.set("search", search);
      const r = await api.get<RowsResponse>(
        `/v1/db/table/${selected}?${params}`,
      );
      setRows(r.rows);
      setTotal(r.total);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <MobileBackButton to="dashboard" label="Dashboard" />

      <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: t.text }}>
        Database
      </h1>
      <p style={{ fontSize: 12, color: t.tg, margin: 0 }}>
        Read-only on mobile. Inline cell editing is desktop-only.
      </p>

      {error && (
        <div
          style={{
            padding: "10px 14px",
            background: t.redb,
            border: `1px solid ${t.redt}`,
            color: t.red,
            borderRadius: 10,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {/* Table selector */}
      <MobileChip onClick={() => setTableSheet(true)}>
        Table: {selected || "(pick one)"}
        {selected && tables && (
          <span style={{ color: t.tg, marginLeft: 4 }}>
            · {tables.find((tab) => tab.name === selected)?.row_count ?? "?"} rows
          </span>
        )}
      </MobileChip>

      {selected && (
        <>
          <MobileInput
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search text columns"
            leadingIcon={Ic.search}
            trailing={
              search ? (
                <button
                  onClick={() => setSearch("")}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: t.tg,
                    padding: 4,
                    width: 32,
                    height: 32,
                  }}
                >
                  {Ic.x}
                </button>
              ) : undefined
            }
          />

          {loading && (
            <div style={{ padding: 16, color: t.tg, textAlign: "center" }}>
              Loading…
            </div>
          )}

          {rows?.map((row, i) => {
            const rowKey = row[pkCol] as string | number;
            return (
              <div
                key={`${rowKey}-${i}`}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  padding: 10,
                  background: t.bg2,
                  border: `1px solid ${t.border}`,
                  borderRadius: 10,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                    paddingBottom: 6,
                    borderBottom: `1px solid ${t.borderL}`,
                    marginBottom: 4,
                  }}
                >
                  <span
                    style={{
                      fontSize: 11,
                      color: t.tg,
                      fontFamily: "monospace",
                    }}
                  >
                    {pkCol}: {String(rowKey)}
                  </span>
                  <button
                    onClick={() => deleteRow(rowKey)}
                    style={{
                      background: t.redb,
                      color: t.red,
                      border: `1px solid ${t.redt}`,
                      borderRadius: 6,
                      padding: "4px 10px",
                      fontSize: 11,
                      cursor: "pointer",
                    }}
                  >
                    Delete
                  </button>
                </div>
                {schema?.columns.map((col) => {
                  if (col.primary_key) return null;
                  const v = row[col.name];
                  const display =
                    v === null || v === undefined
                      ? "—"
                      : typeof v === "object"
                        ? JSON.stringify(v)
                        : String(v);
                  return (
                    <div
                      key={col.name}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "100px 1fr",
                        gap: 6,
                        fontSize: 12,
                        padding: "2px 0",
                      }}
                    >
                      <span
                        style={{
                          color: t.tg,
                          textTransform: "uppercase",
                          fontWeight: 600,
                          fontSize: 10,
                          paddingTop: 2,
                        }}
                      >
                        {col.name}
                      </span>
                      <span
                        style={{
                          color: t.text2,
                          wordBreak: "break-word",
                          maxHeight: 60,
                          overflow: "hidden",
                        }}
                      >
                        {display}
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          })}

          {!loading && rows && rows.length === 0 && (
            <div
              style={{
                padding: 24,
                textAlign: "center",
                color: t.tg,
                fontSize: 13,
                background: t.bg2,
                border: `1px solid ${t.borderL}`,
                borderRadius: 12,
              }}
            >
              {search ? "No rows match." : "Empty table."}
            </div>
          )}

          <MobilePagination
            page={page}
            totalPages={totalPages}
            onPrev={() => setPage(Math.max(1, page - 1))}
            onNext={() => setPage(page + 1)}
          />
        </>
      )}

      {/* Table picker sheet */}
      <MobileSheet
        open={tableSheet}
        onClose={() => setTableSheet(false)}
        title="Pick a table"
        height="tall"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {tables?.map((tab) => (
            <MobileRow
              key={tab.name}
              title={tab.name}
              subtitle={`${tab.row_count.toLocaleString()} rows`}
              active={selected === tab.name}
              hideChevron
              onClick={() => {
                loadTable(tab.name);
                setTableSheet(false);
              }}
            />
          ))}
        </div>
      </MobileSheet>
    </div>
  );
}
