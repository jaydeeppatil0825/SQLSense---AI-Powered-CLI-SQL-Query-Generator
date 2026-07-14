import type { QueryRow } from "../../gateway/contracts";
import { formatDisplayValue } from "./formatDisplayValue";

export function ResultTable({ columns, rows }: { columns: string[]; rows: QueryRow[] }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/10">
      <table className="min-w-full divide-y divide-white/10 text-sm">
        <caption className="sr-only">Validated query result table</caption>
        <thead className="bg-slate-950">
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col" className="whitespace-nowrap px-4 py-3 text-left font-semibold text-slate-200">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/10 bg-slate-950/40">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="whitespace-nowrap px-4 py-3 text-slate-300">
                  {formatDisplayValue(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
