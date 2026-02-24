from flask import Blueprint, jsonify, request
from db import get_db
from routes.users import admin_required

admin_bp = Blueprint("admin", __name__)


def get_table_names(db):
    """Return list of user-created table names from the database."""
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r["name"] for r in rows]


def get_column_names(db, table):
    """Return list of column names for a table."""
    rows = db.execute(f"PRAGMA table_info([{table}])").fetchall()
    return [r["name"] for r in rows]


@admin_bp.get("/api/admin/tables")
@admin_required
def list_tables():
    db = get_db()
    try:
        tables = []
        for name in get_table_names(db):
            cols = db.execute(f"PRAGMA table_info([{name}])").fetchall()
            tables.append({
                "name": name,
                "columns": [
                    {"name": c["name"], "type": c["type"], "pk": bool(c["pk"])}
                    for c in cols
                ],
            })
        return jsonify(tables)
    finally:
        db.close()


@admin_bp.get("/api/admin/tables/<table_name>/rows")
@admin_required
def list_rows(table_name):
    db = get_db()
    try:
        # Validate table name against actual schema
        if table_name not in get_table_names(db):
            return jsonify({"error": "Table not found"}), 404

        rows = db.execute(f"SELECT * FROM [{table_name}] ORDER BY id DESC").fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


@admin_bp.put("/api/admin/tables/<table_name>/rows/<int:row_id>")
@admin_required
def update_row(table_name, row_id):
    db = get_db()
    try:
        if table_name not in get_table_names(db):
            return jsonify({"error": "Table not found"}), 404

        valid_columns = get_column_names(db, table_name)
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Filter to only valid, non-id columns
        updates = {k: v for k, v in data.items() if k in valid_columns and k != "id"}
        if not updates:
            return jsonify({"error": "No valid columns to update"}), 400

        set_clause = ", ".join(f"[{col}] = ?" for col in updates)
        values = list(updates.values()) + [row_id]

        db.execute(
            f"UPDATE [{table_name}] SET {set_clause} WHERE id = ?", values
        )
        db.commit()

        row = db.execute(
            f"SELECT * FROM [{table_name}] WHERE id = ?", (row_id,)
        ).fetchone()
        return jsonify(dict(row) if row else {})
    finally:
        db.close()


@admin_bp.delete("/api/admin/tables/<table_name>/rows/<int:row_id>")
@admin_required
def delete_row(table_name, row_id):
    db = get_db()
    try:
        if table_name not in get_table_names(db):
            return jsonify({"error": "Table not found"}), 404

        db.execute(f"DELETE FROM [{table_name}] WHERE id = ?", (row_id,))
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()
