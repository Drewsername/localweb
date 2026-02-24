import { useState, useEffect } from "react";
import { Link } from "react-router-dom";

interface Column {
  name: string;
  type: string;
  pk: boolean;
}

interface Table {
  name: string;
  columns: Column[];
}

export default function Admin() {
  const [tables, setTables] = useState<Table[]>([]);
  const [selected, setSelected] = useState("");
  const [columns, setColumns] = useState<Column[]>([]);
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/admin/tables")
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load tables");
        return r.json();
      })
      .then((data: Table[]) => {
        setTables(data);
        if (data.length > 0) {
          setSelected(data[0].name);
          setColumns(data[0].columns);
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selected) return;
    const table = tables.find((t) => t.name === selected);
    if (table) setColumns(table.columns);
    fetchRows(selected);
  }, [selected]);

  function fetchRows(table: string) {
    fetch(`/api/admin/tables/${table}/rows`)
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load rows");
        return r.json();
      })
      .then(setRows)
      .catch((e) => setError(e.message));
  }

  function startEdit(row: Record<string, unknown>) {
    setEditingId(row.id as number);
    const vals: Record<string, string> = {};
    columns.forEach((c) => {
      vals[c.name] = row[c.name] == null ? "" : String(row[c.name]);
    });
    setEditValues(vals);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditValues({});
  }

  async function saveEdit() {
    if (editingId == null) return;
    const payload: Record<string, string> = {};
    columns.forEach((c) => {
      if (c.name !== "id") payload[c.name] = editValues[c.name];
    });

    const res = await fetch(`/api/admin/tables/${selected}/rows/${editingId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json();
      setError(err.error || "Update failed");
      return;
    }
    cancelEdit();
    fetchRows(selected);
  }

  async function deleteRow(id: number) {
    if (!confirm("Delete this row?")) return;
    const res = await fetch(`/api/admin/tables/${selected}/rows/${id}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const err = await res.json();
      setError(err.error || "Delete failed");
      return;
    }
    fetchRows(selected);
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl">&larr;</Link>
          <h1 className="text-2xl font-bold">Admin</h1>
        </div>

        {error && (
          <div className="p-3 bg-red-900/50 border border-red-700 rounded-lg text-red-200 text-sm">
            {error}
            <button onClick={() => setError("")} className="ml-2 text-red-400 hover:text-red-200">&times;</button>
          </div>
        )}

        {/* Table selector */}
        <div className="flex flex-wrap gap-2">
          {tables.map((t) => (
            <button
              key={t.name}
              onClick={() => { setSelected(t.name); cancelEdit(); }}
              className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
                selected === t.name
                  ? "bg-red-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:text-white"
              }`}
            >
              {t.name}
            </button>
          ))}
        </div>

        {/* Data table */}
        {selected && (
          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-800">
                  {columns.map((c) => (
                    <th key={c.name} className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                      {c.name}
                    </th>
                  ))}
                  <th className="px-4 py-2 text-right text-xs font-medium text-gray-400 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id as number} className="bg-gray-900 border-b border-gray-800">
                    {columns.map((c) => (
                      <td key={c.name} className="px-4 py-2 text-gray-300 whitespace-nowrap">
                        {editingId === row.id && c.name !== "id" ? (
                          <input
                            type="text"
                            value={editValues[c.name] ?? ""}
                            onChange={(e) => setEditValues({ ...editValues, [c.name]: e.target.value })}
                            className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-red-500"
                          />
                        ) : (
                          <span>{row[c.name] == null ? <span className="text-gray-600 italic">null</span> : String(row[c.name])}</span>
                        )}
                      </td>
                    ))}
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      {editingId === row.id ? (
                        <div className="flex gap-2 justify-end">
                          <button onClick={saveEdit} className="px-2 py-1 bg-green-700 hover:bg-green-600 rounded text-xs text-white">Save</button>
                          <button onClick={cancelEdit} className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs text-white">Cancel</button>
                        </div>
                      ) : (
                        <div className="flex gap-2 justify-end">
                          <button onClick={() => startEdit(row)} className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs text-white">Edit</button>
                          <button onClick={() => deleteRow(row.id as number)} className="px-2 py-1 bg-red-800 hover:bg-red-700 rounded text-xs text-white">Delete</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={columns.length + 1} className="px-4 py-8 text-center text-gray-500">
                      No rows
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
