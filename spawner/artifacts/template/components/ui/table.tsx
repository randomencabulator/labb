import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type Column<T> = {
  // The row's field to show, or a function of the row
  key: keyof T & string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  align?: "left" | "right";
};

// A table of rows, typed by the row: <Table rows={orders} columns={[{ key: "id", header: "Order" }]} />
export function Table<T>({ rows, columns, rowKey, className }: { rows: readonly T[]; columns: readonly Column<T>[]; rowKey?: (row: T, index: number) => string | number; className?: string }) {
  return (
    <div className={cn("overflow-x-auto rounded-lg border", className)}>
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={cn("px-4 py-2.5 font-medium", column.align === "right" && "text-right")}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey ? rowKey(row, i) : i} className="border-t hover:bg-muted/30">
              {columns.map((column) => (
                <td key={column.key} className={cn("px-4 py-2.5", column.align === "right" && "text-right tabular-nums")}>
                  {column.render ? column.render(row) : String(row[column.key] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
